"""Folder list screen: auto-discovers image folders and lets user select which to scan."""

import os
import threading

from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.checkbox import CheckBox
from kivy.uix.popup import Popup
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
from kivy.clock import Clock
from kivy.app import App

from ..discovery import discover_folders, FolderInfo

KV = '''
<FolderListScreen>:
    orientation: 'vertical'
    padding: dp(10)
    spacing: dp(6)

    # Title
    Label:
        text: '\\U0001F4C1 选择扫描文件夹'
        font_size: dp(20)
        size_hint_y: None
        height: dp(40)
        color: 0.1, 0.45, 0.82, 1

    # Status
    Label:
        id: status_label
        text: root.status_text
        font_size: dp(12)
        size_hint_y: None
        height: dp(24)
        color: 0.5, 0.5, 0.5, 1

    # Folder list
    ScrollView:
        id: scroll_view
        do_scroll_x: False
        BoxLayout:
            id: folder_container
            orientation: 'vertical'
            spacing: dp(4)
            size_hint_y: None
            height: self.minimum_height
            padding: dp(2)

    # Bottom bar
    BoxLayout:
        size_hint_y: None
        height: dp(42)
        spacing: dp(6)

        Button:
            text: '全选'
            size_hint_x: 0.25
            font_size: dp(12)
            background_normal: ''
            background_color: 0.4, 0.4, 0.4, 1
            color: 1, 1, 1, 1
            on_release: root.select_all()

        Button:
            text: '取消全选'
            size_hint_x: 0.25
            font_size: dp(12)
            background_normal: ''
            background_color: 0.4, 0.4, 0.4, 1
            color: 1, 1, 1, 1
            on_release: root.deselect_all()

        Button:
            id: scan_btn
            text: root.button_text
            size_hint_x: 0.5
            font_size: dp(14)
            background_normal: ''
            background_color: 0.1, 0.45, 0.82, 1
            color: 1, 1, 1, 1
            on_release: root.start_scan()
            disabled: root.scanning
'''


class FolderRow(BoxLayout):
    """A single folder row: checkbox + name + image count."""

    def __init__(self, folder: FolderInfo, screen: 'FolderListScreen', **kwargs):
        super().__init__(
            orientation='horizontal',
            size_hint_y=None,
            height='40dp',
            spacing='6dp',
            **kwargs
        )
        self.folder = folder
        self.screen = screen
        self._checked = True  # Default: selected

        # Checkbox
        self.cb = CheckBox(
            active=True,
            size_hint_x=None,
            width='36dp',
            color=(0.1, 0.45, 0.82, 1)
        )
        self.cb.bind(active=self._on_check)
        self.add_widget(self.cb)

        # Folder info
        info = Label(
            text=f'{folder.name}  ({folder.image_count} 张)',
            size_hint_x=1,
            halign='left', valign='middle',
            font_size='13sp',
            color=(0.2, 0.2, 0.2, 1),
            shorten=True,
            shorten_from='right'
        )
        self.add_widget(info)

        # Make row tappable
        self._tap_area = Button(
            text='',
            size_hint_x=1,
            background_normal='',
            background_color=(1, 1, 1, 0),
            opacity=1
        )
        self._tap_area.bind(on_release=self._toggle)
        self.add_widget(self._tap_area)

    @property
    def checked(self) -> bool:
        return self._checked

    def _on_check(self, instance, value):
        self._checked = value
        self.screen._update_counts()

    def _toggle(self, instance):
        self._checked = not self._checked
        self.cb.active = self._checked
        self.screen._update_counts()

    def set_checked(self, value: bool):
        self._checked = value
        self.cb.active = value


class FolderListScreen(BoxLayout, Screen):
    """First screen: discovers image folders and lets user select which to scan."""

    status_text = StringProperty('正在发现文件夹...')
    button_text = StringProperty('开始扫描')
    scanning = BooleanProperty(False)
    selected_count = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._folders: list[FolderInfo] = []
        self._rows: list[FolderRow] = []
        self._discovered = False
        self._discovery_thread: threading.Thread | None = None

    def on_enter(self, *args):
        """Start folder discovery when screen is first shown."""
        if not self._discovered:
            self._start_discovery()

    def _start_discovery(self):
        """Launch folder discovery in background thread."""
        self.status_text = '正在发现文件夹...'
        self._discovery_thread = threading.Thread(
            target=self._run_discovery, daemon=True
        )
        self._discovery_thread.start()

    def _run_discovery(self):
        """Background: discover image folders."""
        try:
            def on_progress(msg):
                Clock.schedule_once(lambda dt: setattr(self, 'status_text', msg))

            folders = discover_folders(progress_callback=on_progress)
            Clock.schedule_once(lambda dt: self._on_folders_found(folders))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_discovery_error(str(e)))

    def _on_folders_found(self, folders: list[FolderInfo]):
        """Populate the folder list on the main thread."""
        self._folders = folders
        self._discovered = True
        container = self.ids.folder_container
        container.clear_widgets()
        self._rows.clear()

        if not folders:
            self.status_text = '没有发现包含图片的文件夹'
            return

        self.status_text = f'发现 {len(folders)} 个文件夹，共 {sum(f.image_count for f in folders)} 张图片'

        for folder in folders:
            row = FolderRow(folder=folder, screen=self)
            self._rows.append(row)
            container.add_widget(row)

        self._update_counts()

    def _on_discovery_error(self, error: str):
        self.status_text = f'发现文件夹时出错: {error}'
        self._discovered = True

    def _update_counts(self):
        """Update selected count and button text."""
        count = sum(1 for r in self._rows if r.checked)
        self.selected_count = count
        if count > 0:
            self.button_text = f'开始扫描 ({count} 个文件夹)'
        else:
            self.button_text = '开始扫描'

    def select_all(self):
        for row in self._rows:
            row.set_checked(True)
        self._update_counts()

    def deselect_all(self):
        for row in self._rows:
            row.set_checked(False)
        self._update_counts()

    def start_scan(self):
        """Collect checked paths and navigate to ScanScreen."""
        checked_paths = [row.folder.path for row in self._rows if row.checked]
        if not checked_paths:
            popup = Popup(
                title='提示',
                content=Label(text='请至少选择一个文件夹'),
                size_hint=(0.6, 0.3)
            )
            popup.open()
            return

        app = App.get_running_app()
        scan_screen = app.root.get_screen('scan')
        scan_screen.set_selected_folders(checked_paths)
        app.root.current = 'scan'
