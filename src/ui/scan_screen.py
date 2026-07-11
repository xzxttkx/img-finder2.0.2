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
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
from kivy.clock import Clock
from kivy.app import App
from kivy.utils import platform

from ..scanner import ImageScanner, ImageFile
from ..storage import get_default_scan_path, create_scanner
from ..db import ScanCache
from ..detector import DuplicateDetector, DuplicateReport

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

    # Folder summary row — shows how many folders are selected
    BoxLayout:
        size_hint_y: None
        height: dp(40)
        spacing: dp(6)
        Label:
            text: '文件夹:'
            size_hint_x: None
            width: dp(54)
            font_size: dp(13)
            text_size: self.size
            halign: 'right'
            valign: 'middle'
        Label:
            id: folder_label
            text: root.folder_text
            font_size: dp(12)
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'right'
            color: 0.3, 0.5, 0.9, 1
        Button:
            text: '选择'
            size_hint_x: None
            width: dp(52)
            font_size: dp(12)
            on_release: app.root.current = 'folder_list'

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

    # Cancel button (only visible during scan)
    Button:
        id: cancel_button
        text: '取消扫描'
        size_hint_y: None
        height: dp(36)
        font_size: dp(13)
        background_normal: ''
        background_color: 0.9, 0.3, 0.3, 1
        color: 1, 1, 1, 1
        on_release: root.cancel_scan()
        opacity: 1 if root.scanning else 0
        disabled: not root.scanning

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

    folder_text = StringProperty('尚未选择文件夹')
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
        self._scanner = None
        self._detector: DuplicateDetector | None = None
        self._cache: ScanCache | None = None
        self._report: DuplicateReport | None = None
        self._scan_thread: threading.Thread | None = None
        self._selected_folders: list[str] = []

        # Set default single path for first launch (before folder list is ready)
        self._set_default_paths()

        # Initialize cache when app data dir is available
        Clock.schedule_once(self._init_cache, 0.5)

    @property
    def _is_android(self) -> bool:
        return platform == 'android'

    def _set_default_paths(self):
        """Set initial default path(s) in case user doesn't go through folder list."""
        default = get_default_scan_path()
        if default:
            self._selected_folders = [default]
            self.folder_text = os.path.basename(default) or default

    def set_selected_folders(self, folders: list[str]):
        """Called by FolderListScreen with the user's checked folders."""
        self._selected_folders = folders
        if len(folders) == 1:
            self.folder_text = os.path.basename(folders[0])
        elif len(folders) > 1:
            names = ', '.join(os.path.basename(f) for f in folders[:3])
            if len(folders) > 3:
                names += f' 等{len(folders)}个'
            self.folder_text = names
        else:
            self.folder_text = '尚未选择文件夹'

        if hasattr(self, 'ids') and 'folder_label' in self.ids:
            self.ids.folder_label.text = self.folder_text

    def _init_cache(self, dt):
        app = App.get_running_app()
        if app and app.user_data_dir:
            self._cache = ScanCache(app.user_data_dir)

    def on_threshold_change(self, value):
        self.threshold = int(value)
        if hasattr(self, 'ids') and 'threshold_label' in self.ids:
            self.ids.threshold_label.text = str(int(value))

    def start_scan(self):
        """Start the scan in a background thread."""
        if self.scanning:
            return

        if not self._selected_folders:
            popup = Popup(
                title='提示',
                content=Label(text='请先点击"选择"来勾选要扫描的文件夹'),
                size_hint=(0.7, 0.35),
            )
            popup.open()
            return

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

    def cancel_scan(self):
        """Cancel the current scan."""
        if self._scanner and hasattr(self._scanner, 'cancel'):
            self._scanner.cancel()
        if self._detector:
            self._detector.cancel()

    def _run_scan(self):
        """Background scan worker — iterates over all selected folders."""
        try:
            all_images: list[ImageFile] = []
            total_folders = len(self._selected_folders)
            seen_paths: set[str] = set()

            for fi, folder in enumerate(self._selected_folders):
                if self._scanner and hasattr(self._scanner, '_cancelled') and self._scanner._cancelled:
                    break

                Clock.schedule_once(
                    lambda dt, i=fi, f=folder: self._update_folder_progress(i, f, total_folders)
                )

                def on_scan_progress(count, current_path):
                    Clock.schedule_once(
                        lambda dt, c=count, p=current_path: self._update_scan_progress(c, p)
                    )

                if self._is_android:
                    scanner = create_scanner()
                    images = scanner.scan(folder, on_scan_progress)
                else:
                    scanner = ImageScanner()
                    images = scanner.scan(folder, on_scan_progress)

                # Deduplicate by path across folders
                for img in images:
                    if img.path not in seen_paths:
                        seen_paths.add(img.path)
                        if hasattr(img, 'path'):
                            all_images.append(ImageFile(
                                path=img.path,
                                file_size=img.file_size,
                                modified_time=img.modified_time
                            ))

            if hasattr(scanner, '_cancelled') and scanner._cancelled:
                Clock.schedule_once(lambda dt: self._on_scan_cancelled())
                return

            if not all_images:
                Clock.schedule_once(lambda dt: self._on_no_images())
                return

            # Phase 2: Detect duplicates
            if self._cache:
                self._detector = DuplicateDetector(cache=self._cache)
            else:
                self._detector = DuplicateDetector()

            def on_detect_progress(phase, current, total):
                Clock.schedule_once(lambda dt: self._update_detect_progress(phase, current, total))

            self._report = self._detector.detect(
                all_images,
                threshold=self.threshold,
                on_progress=on_detect_progress
            )

            Clock.schedule_once(lambda dt: self._on_scan_complete())

        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_scan_error(str(e)))

    def _update_folder_progress(self, idx: int, folder: str, total: int):
        name = os.path.basename(folder)
        self.status_text = f'扫描文件夹 {idx+1}/{total}: {name}'

    def _update_scan_progress(self, count: int, current_path: str):
        self.progress_text = f'已找到 {count} 张图片...'

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
        self.status_text = '选中的文件夹中没有找到图片文件'
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
