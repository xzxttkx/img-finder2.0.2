"""Scan screen: folder selection, threshold slider, scan trigger, and progress display."""

import os
import threading

from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
from kivy.clock import Clock
from kivy.app import App
from kivy.utils import platform

from ..scanner import ImageScanner, ImageFile
from ..storage import get_default_scan_path, is_android_scoped_storage, create_scanner
from ..db import ScanCache
from ..detector import DuplicateDetector, DuplicateReport
from ..exporter import ReportExporter

KV = '''
<ScanScreen>:
    orientation: 'vertical'
    padding: dp(10)
    spacing: dp(6)

    # Title
    Label:
        text: '\\U0001F50D 重复图片查找器'
        font_size: dp(20)
        size_hint_y: None
        height: dp(40)
        color: 0.1, 0.45, 0.82, 1

    # Path selection row — taller on mobile so long paths wrap instead of truncating
    BoxLayout:
        size_hint_y: None
        height: dp(54)
        spacing: dp(6)
        Label:
            text: '目录:'
            size_hint_x: None
            width: dp(44)
            font_size: dp(13)
            text_size: self.size
            halign: 'right'
            valign: 'middle'
        Label:
            id: path_label
            text: root.scan_path
            font_size: dp(11)
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'center'
            color: 0.4, 0.4, 0.4, 1
        Button:
            text: '选'
            size_hint_x: None
            width: dp(48)
            font_size: dp(13)
            on_release: root.open_path_picker()

    # Threshold slider
    BoxLayout:
        size_hint_y: None
        height: dp(40)
        spacing: dp(6)
        Label:
            text: '阈值:'
            size_hint_x: None
            width: dp(44)
            font_size: dp(13)
            text_size: self.size
            halign: 'right'
            valign: 'middle'
        Slider:
            id: threshold_slider
            min: 0
            max: 32
            value: root.threshold
            step: 1
            on_value: root.on_threshold_change(self.value)
        Label:
            id: threshold_label
            text: str(root.threshold)
            size_hint_x: None
            width: dp(28)
            font_size: dp(13)
            text_size: self.size
            halign: 'center'
            valign: 'middle'

    # Scan button
    Button:
        id: scan_button
        text: '\\U0001F50D 开始扫描'
        size_hint_y: None
        height: dp(48)
        font_size: dp(16)
        background_normal: ''
        background_color: 0.1, 0.45, 0.82, 1
        color: 1, 1, 1, 1
        on_release: root.start_scan()
        disabled: root.scanning

    # Progress
    BoxLayout:
        size_hint_y: None
        height: dp(30)
        spacing: dp(6)
        ProgressBar:
            id: progress_bar
            value: root.progress
            max: 100
        Label:
            id: progress_label
            text: root.progress_text
            size_hint_x: None
            width: dp(110)
            font_size: dp(11)
            text_size: self.size
            halign: 'left'
            valign: 'middle'

    # Status text
    Label:
        id: status_label
        text: root.status_text
        size_hint_y: None
        height: dp(20)
        font_size: dp(12)
        color: 0.5, 0.5, 0.5, 1

    # Spacer
    BoxLayout:
        size_hint_y: 1

    # Results quick summary (shown after scan)
    BoxLayout:
        id: summary_box
        size_hint_y: None
        height: dp(72)
        spacing: dp(12)
        opacity: 1 if root.has_results else 0
        disabled: not root.has_results

        BoxLayout:
            orientation: 'vertical'
            size_hint_x: 1
            Label:
                text: '发现重复'
                font_size: dp(12)
                color: 0.5, 0.5, 0.5, 1
                size_hint_y: None
                height: dp(20)
            Label:
                id: dup_count_label
                text: str(root.dup_count) + ' 组'
                font_size: dp(20)
                bold: True
                color: 0.82, 0.15, 0.15, 1
                size_hint_y: None
                height: dp(32)

        BoxLayout:
            orientation: 'vertical'
            size_hint_x: 1
            Label:
                text: '可释放空间'
                font_size: dp(12)
                color: 0.5, 0.5, 0.5, 1
                size_hint_y: None
                height: dp(20)
            Label:
                id: wasted_label
                text: root.wasted_text
                font_size: dp(20)
                bold: True
                color: 0.82, 0.15, 0.15, 1
                size_hint_y: None
                height: dp(32)

        Button:
            text: '查看结果 >'
            size_hint_x: None
            width: dp(120)
            background_normal: ''
            background_color: 0.1, 0.45, 0.82, 1
            color: 1, 1, 1, 1
            on_release: root.view_results()
'''


class ScanScreen(BoxLayout, Screen):
    """Main scan screen where users configure and run duplicate detection."""

    scan_path = StringProperty('')
    threshold = NumericProperty(10)
    progress = NumericProperty(0)
    progress_text = StringProperty('')
    status_text = StringProperty('')
    scanning = BooleanProperty(False)
    has_results = BooleanProperty(False)
    dup_count = NumericProperty(0)
    wasted_text = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._scanner = None  # Created per-scan to pick the right backend
        self._detector: DuplicateDetector | None = None
        self._cache: ScanCache | None = None
        self._report: DuplicateReport | None = None
        self._scan_thread: threading.Thread | None = None

        # Determine default scan path based on platform
        self.scan_path = self._get_default_path()

        # Initialize cache when app data dir is available
        Clock.schedule_once(self._init_cache, 0.5)

    @property
    def _is_android(self) -> bool:
        return platform == 'android'

    def _get_default_path(self) -> str:
        """Return the default scan path for this platform."""
        return get_default_scan_path()

    def _init_cache(self, dt):
        app = App.get_running_app()
        if app and app.user_data_dir:
            self._cache = ScanCache(app.user_data_dir)

    def on_threshold_change(self, value):
        self.threshold = int(value)
        # Update label via ids
        if hasattr(self, 'ids') and 'threshold_label' in self.ids:
            self.ids.threshold_label.text = str(int(value))

    def open_path_picker(self):
        """Open a path picker dialog.

        On Android: shows a text input popup (Kivy's FileChooser doesn't work
        well with scoped storage). The user can type/paste a path or use the
        default DCIM/Pictures path.
        On Desktop: uses Kivy's native FileChooserListView.
        """
        # On Android, use a text input dialog instead of FileChooser
        if platform == 'android':
            self._open_android_path_picker()
        else:
            self._open_desktop_file_chooser()

    def _open_android_path_picker(self):
        """Android path picker: text input with common path suggestions."""
        content = BoxLayout(orientation='vertical', spacing='8dp', padding='8dp')

        content.add_widget(Label(
            text='输入或粘贴扫描目录路径:',
            size_hint_y=None, height='30dp',
            halign='left', valign='middle',
            font_size='14sp',
            color=(0.2, 0.2, 0.2, 1)
        ))

        path_input = TextInput(
            text=self.scan_path,
            multiline=False,
            size_hint_y=None,
            height='40dp',
            font_size='14sp'
        )
        content.add_widget(path_input)

        # Quick-select buttons for common Android paths
        quick_paths = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            height='120dp',
            spacing='4dp'
        )
        quick_paths.add_widget(Label(
            text='快捷路径:',
            size_hint_y=None, height='24dp',
            halign='left', valign='middle',
            font_size='12sp',
            color=(0.5, 0.5, 0.5, 1)
        ))

        android_paths = [
            ('📷 DCIM', '/storage/emulated/0/DCIM'),
            ('🖼 Pictures', '/storage/emulated/0/Pictures'),
            ('📥 Download', '/storage/emulated/0/Download'),
        ]

        for label, p in android_paths:
            btn = Button(
                text=f'  {label}:  {p}',
                size_hint_y=None,
                height='28dp',
                font_size='11sp',
                halign='left',
                valign='middle',
                background_normal='',
                background_color=(0.95, 0.95, 0.95, 1),
                color=(0.15, 0.45, 0.82, 1)
            )
            btn.bind(on_release=lambda x, path=p: setattr(path_input, 'text', path))
            btn.text_size = (btn.width, None)
            quick_paths.add_widget(btn)

        content.add_widget(quick_paths)

        # Buttons row
        buttons = BoxLayout(
            size_hint_y=None, height='40dp', spacing='8dp'
        )

        popup = None  # Forward ref for closure

        def on_confirm(instance):
            path = path_input.text.strip()
            if path:
                self.scan_path = path
                self.ids.path_label.text = path
            popup.dismiss() if popup else None

        buttons.add_widget(Button(
            text='取消',
            background_normal='',
            background_color=(0.6, 0.6, 0.6, 1),
            color=(1, 1, 1, 1),
            on_release=lambda x: popup.dismiss() if popup else None
        ))
        buttons.add_widget(Button(
            text='确认',
            background_normal='',
            background_color=(0.1, 0.45, 0.82, 1),
            color=(1, 1, 1, 1),
            on_release=on_confirm
        ))
        content.add_widget(buttons)

        popup = Popup(
            title='选择扫描目录',
            content=content,
            size_hint=(0.88, 0.55),
        )
        popup.open()

    def _open_desktop_file_chooser(self):
        """Desktop file chooser using Kivy's FileChooserListView."""
        from kivy.uix.filechooser import FileChooserListView

        start_path = self.scan_path or os.path.expanduser('~')

        chooser_layout = BoxLayout(orientation='vertical', spacing=8)
        filechooser = FileChooserListView(
            path=start_path,
            dirselect=True,
            filters=[''],
        )
        chooser_layout.add_widget(filechooser)

        btn_layout = BoxLayout(size_hint_y=None, height='48dp', spacing=8)

        popup = None  # Forward ref for closure

        def on_select(instance):
            selected = filechooser.selection
            if selected:
                self.scan_path = selected[0]
                self.ids.path_label.text = selected[0]
            popup.dismiss() if popup else None

        btn_cancel = Button(text='取消', on_release=lambda x: popup.dismiss() if popup else None)
        btn_select = Button(
            text='选择此文件夹',
            background_normal='',
            background_color=(0.1, 0.45, 0.82, 1),
            color=(1, 1, 1, 1),
            on_release=on_select
        )
        btn_layout.add_widget(btn_cancel)
        btn_layout.add_widget(btn_select)
        chooser_layout.add_widget(btn_layout)

        popup = Popup(
            title='选择扫描目录',
            content=chooser_layout,
            size_hint=(0.9, 0.8),
        )
        popup.open()

    def start_scan(self):
        """Start the scan in a background thread."""
        if self.scanning:
            return

        # On Android with scoped storage, the directory may not be directly
        # readable via os.path — MediaStore will be used instead.
        # On desktop, require a valid existing directory.
        if not self._is_android and (not self.scan_path or not os.path.isdir(self.scan_path)):
            popup = Popup(
                title='错误',
                content=Label(text='请选择一个有效的目录'),
                size_hint=(0.6, 0.3),
            )
            popup.open()
            return

        if self._is_android and (not self.scan_path):
            # On Android, default to DCIM if path is empty
            self.scan_path = get_default_scan_path()
            self.ids.path_label.text = self.scan_path

        self.scanning = True
        self.has_results = False
        self.progress = 0
        self.progress_text = '正在扫描文件...'
        self.status_text = ''

        self._scan_thread = threading.Thread(
            target=self._run_scan,
            daemon=True
        )
        self._scan_thread.start()

    def _run_scan(self):
        """Background scan worker."""
        try:
            # Phase 1: Scan files using the appropriate scanner for this platform
            def on_scan_progress(count, current_path):
                Clock.schedule_once(lambda dt: self._update_scan_progress(count, current_path))

            if self._is_android:
                # Use MediaStore-based scanner on Android (scoped-storage safe)
                self._scanner = create_scanner()
                # MediaStore returns ImageFile-compatible objects
                images = self._scanner.scan(self.scan_path, on_scan_progress)
            else:
                # Use filesystem scanner on desktop
                self._scanner = ImageScanner()
                images = self._scanner.scan(self.scan_path, on_scan_progress)

            if hasattr(self._scanner, '_cancelled') and self._scanner._cancelled:
                Clock.schedule_once(lambda dt: self._on_scan_cancelled())
                return

            if not images:
                Clock.schedule_once(lambda dt: self._on_no_images())
                return

            # On Android, convert storage.ImageFile objects to scanner.ImageFile
            # if needed for DuplicateDetector compatibility
            if self._is_android and images:
                converted = []
                for img in images:
                    converted.append(ImageFile(
                        path=img.path,
                        file_size=img.file_size,
                        modified_time=img.modified_time
                    ))
                images = converted

            # Phase 2: Detect duplicates
            if self._cache:
                self._detector = DuplicateDetector(cache=self._cache)
            else:
                self._detector = DuplicateDetector()

            def on_detect_progress(phase, current, total):
                Clock.schedule_once(lambda dt: self._update_detect_progress(phase, current, total))

            self._report = self._detector.detect(
                images,
                threshold=self.threshold,
                on_progress=on_detect_progress
            )

            # Phase 3: Update UI with results
            Clock.schedule_once(lambda dt: self._on_scan_complete())

        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_scan_error(str(e)))

    def _update_scan_progress(self, count: int, current_path: str):
        self.progress_text = f'已找到 {count} 张图片...'
        self.status_text = current_path if current_path else ''

    def _update_detect_progress(self, phase: str, current: int, total: int):
        if total > 0:
            self.progress = min((current / total) * 100, 99)
        self.progress_text = phase

    def _on_scan_complete(self):
        self.scanning = False
        self.progress = 100

        if self._report:
            self.has_results = True
            self.dup_count = self._report.total_duplicate_groups
            self.wasted_text = self._format_size(self._report.total_wasted_bytes)
            self.status_text = (
                f'扫描完成: {self._report.total_images} 张图片, '
                f'{self._report.total_duplicate_groups} 组重复'
            )
            self.progress_text = '扫描完成!'

    def _on_no_images(self):
        self.scanning = False
        self.progress = 100
        self.status_text = '选中的目录中没有找到图片文件'
        self.progress_text = ''

    def _on_scan_cancelled(self):
        self.scanning = False
        self.progress = 0
        self.status_text = '扫描已取消'
        self.progress_text = ''

    def _on_scan_error(self, error: str):
        self.scanning = False
        self.progress = 0
        self.status_text = f'扫描出错: {error}'
        self.progress_text = ''
        popup = Popup(
            title='扫描错误',
            content=Label(text=f'扫描过程中发生错误:\n{error}'),
            size_hint=(0.7, 0.4),
        )
        popup.open()

    def view_results(self):
        """Navigate to the results screen."""
        if not self._report:
            return

        app = App.get_running_app()
        results_screen = app.root.get_screen('results')
        results_screen.set_report(self._report, self._cache)
        app.root.current = 'results'

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f'{size_bytes} B'
        elif size_bytes < 1024 * 1024:
            return f'{size_bytes / 1024:.1f} KB'
        elif size_bytes < 1024 * 1024 * 1024:
            return f'{size_bytes / (1024 * 1024):.1f} MB'
        else:
            return f'{size_bytes / (1024 * 1024 * 1024):.2f} GB'
