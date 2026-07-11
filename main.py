#!/usr/bin/env python3
"""
Duplicate Image Finder - Android App
Finds exact and visually similar duplicate images on your device.

Usage:
    python main.py          # Run on desktop for testing
    buildozer android debug # Build Android APK
"""

import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.utils import platform

# Material Design color palette
COLORS = {
    'primary': (0.098, 0.455, 0.824, 1),      # #1976D2 Blue
    'primary_dark': (0.059, 0.333, 0.627, 1),  # #0D47A1
    'accent': (1.0, 0.596, 0.0, 1),             # #FF9800 Orange
    'danger': (0.961, 0.263, 0.212, 1),         # #F44336 Red
    'success': (0.298, 0.686, 0.314, 1),        # #4CAF50 Green
    'background': (0.961, 0.961, 0.961, 1),     # #F5F5F5
    'surface': (1.0, 1.0, 1.0, 1),             # White
    'text_primary': (0.129, 0.129, 0.129, 1),
    'text_secondary': (0.459, 0.459, 0.459, 1),
}


def _is_android() -> bool:
    """Return True if running on Android."""
    return platform == 'android'


def _setup_cjk_font():
    """Register a CJK font that covers both Latin and Chinese.

    - Android: use bundled assets/CJKFont.otf (downloaded during CI build).
      Falls back to system DroidSansFallback.ttf on older builds.
    - Windows: use system CJK font (Microsoft YaHei etc.).
    """
    import platform as pf
    system = pf.system()

    if _is_android():
        # 1st priority: bundled font in assets/ (has both Latin + CJK)
        asset_font = os.path.join(os.path.dirname(__file__), 'assets', 'CJKFont.ttf')
        if os.path.exists(asset_font):
            LabelBase.register(name='CJK', fn_regular=asset_font)
            return True
        # 2nd: try system TTF (may be CJK-only, but better than nothing)
        for path in ('/system/fonts/DroidSansFallback.ttf',):
            if os.path.exists(path):
                try:
                    LabelBase.register(name='CJK', fn_regular=path)
                    return True
                except Exception:
                    continue
        return False

    if system == 'Windows':
        windir = os.environ.get('WINDIR', 'C:\\Windows')
        for font_name in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'simsun.ttc'):
            fp = os.path.join(windir, 'Fonts', font_name)
            if os.path.exists(fp):
                LabelBase.register(name='CJK', fn_regular=fp)
                return True

    return False

_FONT_OK = _setup_cjk_font()
# On Android with bundled CJK font: use it as primary (it has both Latin + CJK)
# On Windows: use CJK font as primary
# If no font found: None → use Kivy default Roboto
FONT_NAME = 'CJK' if _FONT_OK else None


class MainScreenManager(ScreenManager):
    """Manages navigation between the three main screens."""
    pass


class DuplicateImageFinderApp(App):
    """Kivy application entry point."""

    title = 'Duplicate Image Finder'
    icon = os.path.join(os.path.dirname(__file__), 'assets', 'icon.png')

    def build(self):
        """Build the app UI."""
        # Import screens (late import to avoid circular deps)
        from src.ui.scan_screen import ScanScreen, KV as SCAN_KV
        from src.ui.results_screen import ResultsScreen, KV as RESULTS_KV, GROUP_CARD_KV
        from src.ui.preview_screen import PreviewScreen, KV as PREVIEW_KV
        from src.ui.folder_list_screen import FolderListScreen, KV as FOLDER_KV

        # Apply KV rules (must be before widget creation)
        from kivy.lang import Builder

        # Inject CJK font globally so Chinese text renders on Windows
        if FONT_NAME:
            Builder.load_string(f'''
<Label>:
    font_name: '{FONT_NAME}'
<Button>:
    font_name: '{FONT_NAME}'
<ToggleButton>:
    font_name: '{FONT_NAME}'
<TextInput>:
    font_name: '{FONT_NAME}'
<CheckBox>:
    font_name: '{FONT_NAME}'
''')

        Builder.load_string(SCAN_KV)
        Builder.load_string(RESULTS_KV)
        Builder.load_string(GROUP_CARD_KV)
        Builder.load_string(PREVIEW_KV)
        Builder.load_string(FOLDER_KV)

        # Set window properties
        Window.clearcolor = COLORS['background']

        # Create screen manager
        sm = MainScreenManager()

        # Add screens
        sm.add_widget(FolderListScreen(name='folder_list'))
        sm.add_widget(ScanScreen(name='scan'))
        sm.add_widget(ResultsScreen(name='results'))
        sm.add_widget(PreviewScreen(name='preview'))

        # Start on folder list screen
        sm.current = 'folder_list'

        # Request Android permissions at startup
        self._request_android_permissions()

        return sm

    def _request_android_permissions(self):
        """Request storage permissions on Android, handling API level differences.

        - Android 13+ (API 33): READ_MEDIA_IMAGES replaces READ_EXTERNAL_STORAGE
        - Android 11+ (API 30): MANAGE_EXTERNAL_STORAGE needed for full file access
        - Android 10  (API 29): READ_EXTERNAL_STORAGE + requestLegacyExternalStorage
        - Android 9-  (API ≤28): READ_EXTERNAL_STORAGE suffices
        """
        if platform != 'android':
            return

        # Determine API level
        api_level = 0
        try:
            from android.os import Build
            api_level = Build.VERSION.SDK_INT
        except Exception:
            try:
                with open('/system/build.prop', 'r') as f:
                    for line in f:
                        if 'ro.build.version.sdk=' in line:
                            api_level = int(line.split('=')[1].strip())
            except Exception:
                api_level = 29  # Assume modern Android

        # Build the permission list based on API level
        try:
            from android.permissions import request_permissions, Permission

            permissions = []

            if api_level >= 33:
                # Android 13+: use granular media permissions
                permissions.append(Permission.READ_MEDIA_IMAGES)
            else:
                permissions.append(Permission.READ_EXTERNAL_STORAGE)

            if api_level >= 30:
                # Android 11+: need MANAGE_EXTERNAL_STORAGE for direct file access
                # This opens the "All files access" settings page for the user
                try:
                    permissions.append(Permission.MANAGE_EXTERNAL_STORAGE)
                except AttributeError:
                    # Some p4a versions may not have this constant
                    pass
            elif api_level <= 28:
                # Android 9 and below only
                permissions.append(Permission.WRITE_EXTERNAL_STORAGE)

            request_permissions(permissions)
        except (ImportError, Exception):
            pass

        # On Android 11+, check MANAGE_EXTERNAL_STORAGE and prompt to enable
        self._check_manage_storage()

    def _check_manage_storage(self):
        """On Android 11+, verify MANAGE_EXTERNAL_STORAGE is granted.
        If not, show a dialog that redirects to system settings."""
        if platform != 'android':
            return

        api_level = 0
        try:
            from android.os import Build
            api_level = Build.VERSION.SDK_INT
        except Exception:
            return

        if api_level < 30:
            return

        try:
            from jnius import autoclass
            Environment = autoclass('android.os.Environment')
            if Environment.isExternalStorageManager():
                return  # Already granted

            # Show dialog asking user to enable it
            from kivy.clock import Clock
            Clock.schedule_once(lambda dt: self._show_manage_storage_dialog(), 2.0)
        except Exception:
            pass

    def _show_manage_storage_dialog(self):
        """Dialog: explain why MANAGE_EXTERNAL_STORAGE is needed and open settings."""
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label

        content = BoxLayout(orientation='vertical', spacing='10dp', padding='10dp')
        content.add_widget(Label(
            text='此应用需要"管理所有文件"权限\n'
                 '才能扫描和删除重复图片。\n\n'
                 '点击"前往设置" > 找到本应用 >\n'
                 '打开"允许管理所有文件"',
            halign='center',
            font_size='14sp'
        ))

        btns = BoxLayout(size_hint_y=None, height='40dp', spacing='8dp')

        def open_settings(instance):
            try:
                from jnius import autoclass
                Intent = autoclass('android.content.Intent')
                Settings = autoclass('android.provider.Settings')
                Uri = autoclass('android.net.Uri')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                activity = PythonActivity.mActivity

                intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                uri_str = f'package:{activity.getPackageName()}'
                intent.setData(Uri.parse(uri_str))
                activity.startActivity(intent)
            except Exception:
                pass
            popup.dismiss()

        btns.add_widget(Button(
            text='稍后再说',
            on_release=lambda x: popup.dismiss()
        ))
        btns.add_widget(Button(
            text='前往设置',
            on_release=open_settings,
            background_normal='',
            background_color=(0.1, 0.45, 0.82, 1),
            color=(1, 1, 1, 1)
        ))
        content.add_widget(btns)

        popup = Popup(
            title='需要权限',
            content=content,
            size_hint=(0.85, 0.5),
        )
        popup.open()

    def on_start(self):
        """Called after build(), when the app is fully initialized."""
        # Ensure user data directory exists
        if not os.path.exists(self.user_data_dir):
            try:
                os.makedirs(self.user_data_dir, exist_ok=True)
            except OSError:
                pass

    def on_pause(self):
        """Handle app going to background (Android)."""
        return True  # Allow the app to be paused

    def on_resume(self):
        """Handle app returning from background (Android)."""
        pass


if __name__ == '__main__':
    # On Android, the app is launched differently — this serves as a
    # desktop test entry point.
    DuplicateImageFinderApp().run()
