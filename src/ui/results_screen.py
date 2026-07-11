"""Results screen: displays duplicate groups with filtering, per-file checkboxes, and batch delete."""

import os

from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.checkbox import CheckBox
from kivy.uix.togglebutton import ToggleButton
from kivy.properties import ObjectProperty, StringProperty, NumericProperty
from kivy.app import App
from kivy.utils import platform


def _safe_delete_file(path: str) -> bool:
    """Delete a file, using MediaStore on Android (scoped storage) or os.remove on desktop.

    Returns True on success, False on failure.
    """
    try:
        if platform == 'android':
            return _android_delete_file(path)
        else:
            os.remove(path)
            return True
    except OSError:
        return False


def _android_delete_file(path: str) -> bool:
    """Delete a file on Android using the MediaStore API (respects scoped storage)."""
    try:
        from jnius import autoclass

        Build = autoclass('android.os.Build')
        if Build.VERSION.SDK_INT >= 30:
            ContentUris = autoclass('android.content.ContentUris')
            MediaStore = autoclass('android.provider.MediaStore')
            MediaStore_Images = MediaStore.Images
            MediaStore_Images_Media = MediaStore_Images.Media

            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()

            uri = MediaStore_Images_Media.EXTERNAL_CONTENT_URI
            projection = [MediaStore_Images_Media._ID]
            selection = f'{MediaStore_Images_Media.DATA}=?'
            selection_args = [path]
            cursor = resolver.query(uri, projection, selection, selection_args, None)

            if cursor and cursor.moveToFirst():
                file_id = cursor.getLong(cursor.getColumnIndex(MediaStore_Images_Media._ID))
                cursor.close()
                delete_uri = ContentUris.withAppendedId(uri, file_id)
                deleted = resolver.delete(delete_uri, None, None)
                return deleted > 0
            elif cursor:
                cursor.close()

            os.remove(path)
            return True
        else:
            os.remove(path)
            return True
    except Exception:
        try:
            os.remove(path)
            return True
        except OSError:
            return False


KV = '''
<ResultsScreen>:
    orientation: 'vertical'
    padding: dp(8)
    spacing: dp(4)

    # Header
    BoxLayout:
        size_hint_y: None
        height: dp(38)
        spacing: dp(6)
        Button:
            text: '< 返回'
            size_hint_x: None
            width: dp(64)
            font_size: dp(12)
            background_normal: ''
            background_color: 0.6, 0.6, 0.6, 1
            color: 1, 1, 1, 1
            on_release: app.root.current = 'scan'
        Label:
            text: '重复 (' + str(root.total_groups) + ' 组)'
            font_size: dp(15)
            size_hint_x: 1
            halign: 'left'
            valign: 'middle'
            text_size: self.size

    # Tab bar: filter by duplicate type
    BoxLayout:
        id: tab_bar
        size_hint_y: None
        height: dp(34)
        spacing: dp(3)

    # Scrollable results list
    ScrollView:
        id: scroll_view
        do_scroll_x: False
        BoxLayout:
            id: groups_container
            orientation: 'vertical'
            spacing: dp(6)
            size_hint_y: None
            height: self.minimum_height
            padding: dp(4)

    # Bottom batch action bar
    BoxLayout:
        id: batch_bar
        size_hint_y: None
        height: dp(40)
        spacing: dp(6)
        opacity: 1 if root.total_checked > 0 else 0.4
        Label:
            id: batch_info
            text: root.batch_info_text
            font_size: dp(11)
            size_hint_x: 1
            halign: 'left'
            valign: 'middle'
            text_size: self.size
            color: 0.8, 0.15, 0.15, 1
        Button:
            text: '删除选中'
            size_hint_x: None
            width: dp(88)
            font_size: dp(12)
            background_normal: ''
            background_color: 0.9, 0.3, 0.3, 1
            color: 1, 1, 1, 1
            on_release: root.batch_delete_checked()
'''

GROUP_CARD_KV = '''
<GroupCard>:
    orientation: 'vertical'
    size_hint_y: None
    height: self.minimum_height
    padding: dp(8)
    spacing: dp(4)
    canvas.before:
        Color:
            rgba: 1, 1, 1, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
    canvas:
        Color:
            rgba: root.card_border_color
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(8))
            width: 1.5
'''


class GroupCard(BoxLayout):
    """A card widget representing one duplicate group with per-file checkboxes."""

    card_border_color = ObjectProperty((0.8, 0.8, 0.8, 1))

    def __init__(self, group, cache, results_screen, **kwargs):
        super().__init__(**kwargs)
        self.group = group
        self.cache = cache
        self.screen = results_screen
        self._expanded = False
        self._checked_paths: set[str] = set()
        self._file_checkboxes: dict[str, CheckBox] = {}

        if group.duplicate_type == 'exact':
            self.card_border_color = (0.3, 0.7, 0.3, 1)
        else:
            self.card_border_color = (1.0, 0.6, 0.1, 1)

        self._build_header()

        # Smart defaults: check all except largest (auto-applied on first expand)
        self._smart_defaults_applied = False

    def _build_header(self):
        g = self.group
        count = len(g.images)
        wasted = self._format_size(g.total_wasted_size)
        dtype = '精确重复' if g.duplicate_type == 'exact' else '视觉相似'

        header = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height='40dp',
            spacing='8dp'
        )

        icon = '\U0001F4F7'
        info = Label(
            text=f'{icon} {dtype} - {count} 张  ({wasted})',
            size_hint_x=1,
            halign='left', valign='middle',
            text_size=(None, None),
            font_size='13sp',
            color=(0.2, 0.2, 0.2, 1)
        )
        header.add_widget(info)

        self._expand_btn = Button(
            text='▶',
            size_hint_x=None,
            width='32dp',
            background_normal='',
            background_color=(0.95, 0.95, 0.95, 1),
            color=(0.4, 0.4, 0.4, 1),
            on_release=lambda x: self._toggle_expand()
        )
        header.add_widget(self._expand_btn)

        self.add_widget(header)

    def _apply_smart_defaults(self):
        """On first expand: uncheck largest file, check all others."""
        if self._smart_defaults_applied or len(self.group.images) < 2:
            return
        self._smart_defaults_applied = True

        largest = max(self.group.images, key=lambda x: x.file_size)
        for img in self.group.images:
            if img.path != largest.path:
                self._checked_paths.add(img.path)

    def _toggle_expand(self):
        self._expanded = not self._expanded

        if self._expanded:
            self._apply_smart_defaults()
            self._expand_btn.text = '▼'
            self._build_expanded_content()
        else:
            self._expand_btn.text = '▶'
            self._collapse_content()

    def _build_expanded_content(self):
        """Show per-file rows with checkboxes."""
        g = self.group
        self._file_checkboxes.clear()

        # File rows
        for img in g.images:
            row = BoxLayout(
                orientation='horizontal',
                size_hint_y=None,
                height='36dp',
                spacing='4dp'
            )

            # Checkbox — checked means "marked for deletion"
            checked = img.path in self._checked_paths
            cb = CheckBox(
                active=checked,
                size_hint_x=None,
                width='32dp',
                color=(0.9, 0.3, 0.3, 1)  # Red tint
            )
            cb.bind(active=lambda instance, value, p=img.path: self._on_check(p, value))
            self._file_checkboxes[img.path] = cb
            row.add_widget(cb)

            # File name + size
            fname = os.path.basename(img.path)
            size_str = self._format_size(img.file_size)
            file_lbl = Label(
                text=f'{fname}  ({size_str})',
                size_hint_x=1,
                halign='left', valign='middle',
                font_size='11sp',
                color=(0.3, 0.3, 0.3, 1),
                shorten=True,
                shorten_from='right'
            )
            row.add_widget(file_lbl)

            # Preview button
            preview_btn = Button(
                text='\U0001F50D',
                size_hint_x=None,
                width='30dp',
                background_normal='',
                background_color=(0.3, 0.5, 0.9, 1),
                color=(1, 1, 1, 1),
                font_size='11sp'
            )
            preview_btn.bind(on_release=lambda x, p=img.path: self._preview_image(p))
            row.add_widget(preview_btn)

            self.add_widget(row)

        # Group action buttons
        actions = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height='32dp',
            spacing='6dp'
        )

        select_all_btn = Button(
            text='全选',
            size_hint_x=1,
            background_normal='',
            background_color=(0.5, 0.5, 0.5, 1),
            color=(1, 1, 1, 1),
            font_size='11sp'
        )
        select_all_btn.bind(on_release=lambda x: self._select_all())
        actions.add_widget(select_all_btn)

        smart_btn = Button(
            text='智能选择',
            size_hint_x=1,
            background_normal='',
            background_color=(0.3, 0.6, 0.8, 1),
            color=(1, 1, 1, 1),
            font_size='11sp'
        )
        smart_btn.bind(on_release=lambda x: self._smart_select())
        actions.add_widget(smart_btn)

        n_checked = len(self._checked_paths)
        del_btn = Button(
            text=f'删除({n_checked})',
            size_hint_x=1,
            background_normal='',
            background_color=(0.9, 0.3, 0.3, 1),
            color=(1, 1, 1, 1),
            font_size='11sp'
        )
        del_btn.bind(on_release=lambda x: self._delete_checked())
        actions.add_widget(del_btn)

        self.add_widget(actions)

    def _collapse_content(self):
        """Remove expanded children (keep only the header)."""
        children_to_remove = self.children[:-1]
        for child in list(children_to_remove):
            self.remove_widget(child)

    def _on_check(self, path: str, value: bool):
        if value:
            self._checked_paths.add(path)
        else:
            self._checked_paths.discard(path)
        self.screen._update_batch_bar()

    def _select_all(self):
        for img in self.group.images:
            self._checked_paths.add(img.path)
        self._sync_checkboxes()
        self.screen._update_batch_bar()

    def _smart_select(self):
        """Uncheck largest, check all others."""
        self._checked_paths.clear()
        if len(self.group.images) < 2:
            return
        largest = max(self.group.images, key=lambda x: x.file_size)
        for img in self.group.images:
            if img.path != largest.path:
                self._checked_paths.add(img.path)
        self._sync_checkboxes()
        self.screen._update_batch_bar()

    def _sync_checkboxes(self):
        """Update all checkbox widgets to match _checked_paths state."""
        for path, cb in self._file_checkboxes.items():
            cb.active = path in self._checked_paths

    def _delete_checked(self):
        """Delete all checked files in this group."""
        if not self._checked_paths:
            return

        to_delete = list(self._checked_paths)
        wasted = self._format_size(
            sum(img.file_size for img in self.group.images if img.path in to_delete)
        )

        def do_delete(confirmed):
            if confirmed:
                deleted = 0
                errors = 0
                for path in to_delete:
                    if _safe_delete_file(path):
                        deleted += 1
                        self.group.images = [i for i in self.group.images if i.path != path]
                        self._checked_paths.discard(path)
                    else:
                        errors += 1

                if len(self.group.images) <= 1:
                    if self.parent:
                        self.parent.remove_widget(self)
                else:
                    self._collapse_content()
                    self._toggle_expand()
                self.screen._update_batch_bar()

                msg = f'已删除 {deleted} 张'
                if errors:
                    msg += f', {errors} 张失败'
                self._show_info(msg)
            popup.dismiss()

        content = BoxLayout(orientation='vertical', spacing='6dp', padding='6dp')
        content.add_widget(Label(
            text=f'删除 {len(to_delete)} 张图片?\n释放 {wasted}',
            halign='center'
        ))
        btns = BoxLayout(size_hint_y=None, height='36dp', spacing='6dp')
        btns.add_widget(Button(text='取消', on_release=lambda x: popup.dismiss()))
        btns.add_widget(Button(
            text='确认删除', on_release=lambda x: do_delete(True),
            background_normal='', background_color=(0.9, 0.3, 0.3, 1),
            color=(1, 1, 1, 1)
        ))
        content.add_widget(btns)
        popup = Popup(title='确认删除', content=content, size_hint=(0.7, 0.35))
        popup.open()

    def _preview_image(self, path):
        """Open the preview screen for a specific image."""
        app = App.get_running_app()
        preview_screen = app.root.get_screen('preview')
        preview_screen.set_group(self.group, path)
        app.root.current = 'preview'

    def get_checked_info(self) -> tuple[int, int]:
        """Return (count of checked files, total bytes of checked files)."""
        count = 0
        total_bytes = 0
        for img in self.group.images:
            if img.path in self._checked_paths:
                count += 1
                total_bytes += img.file_size
        return count, total_bytes

    @staticmethod
    def _show_info(msg):
        content = BoxLayout(orientation='vertical', spacing='6dp', padding='6dp')
        content.add_widget(Label(text=msg))
        btn = Button(text='确定', size_hint_y=None, height='32dp',
                     on_release=lambda x: popup.dismiss())
        content.add_widget(btn)
        popup = Popup(title='提示', content=content, size_hint=(0.6, 0.3))
        popup.open()

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


class ResultsScreen(BoxLayout, Screen):
    """Displays duplicate groups with filtering, per-file checkboxes, and batch delete."""

    total_groups = NumericProperty(0)
    total_checked = NumericProperty(0)
    batch_info_text = StringProperty('')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._report = None
        self._cache = None
        self._all_groups = []
        self._filter = 'all'
        self._tabs_built = False
        self._group_cards: list[GroupCard] = []

    def set_report(self, report, cache=None):
        """Load a scan report into the results screen."""
        self._report = report
        self._cache = cache
        self._all_groups = report.all_groups
        self.total_groups = len(self._all_groups)
        self._filter = 'all'
        if not self._tabs_built:
            self._build_tabs()
            self._tabs_built = True
        self._refresh_list()

    def _build_tabs(self):
        """Build the tab toggle buttons."""
        tab_bar = self.ids.tab_bar
        tab_bar.clear_widgets()

        tabs = [
            ('all', '全部'),
            ('exact', '精确重复'),
            ('similar', '视觉相似'),
        ]

        for filter_key, label in tabs:
            btn = ToggleButton(
                text=label,
                group='filter_tabs',
                state='down' if filter_key == self._filter else 'normal',
                size_hint_x=1,
                background_normal='',
                font_size='12sp'
            )
            btn.bind(on_press=lambda x, k=filter_key: self._on_filter_change(k))
            tab_bar.add_widget(btn)

    def _on_filter_change(self, filter_key):
        self._filter = filter_key
        self._refresh_list()

    def _refresh_list(self):
        """Rebuild the group card list based on current filter."""
        container = self.ids.groups_container
        container.clear_widgets()
        self._group_cards.clear()

        if self._filter == 'exact':
            groups = [g for g in self._all_groups if g.duplicate_type == 'exact']
        elif self._filter == 'similar':
            groups = [g for g in self._all_groups if g.duplicate_type == 'similar']
        else:
            groups = self._all_groups

        if not groups:
            noresult = Label(
                text='没有发现重复图片 \U0001F389',
                size_hint_y=None,
                height='60dp',
                font_size='15sp',
                color=(0.5, 0.5, 0.5, 1)
            )
            container.add_widget(noresult)
            self.total_groups = 0
            self._update_batch_bar()
            return

        for group in groups:
            card = GroupCard(group, self._cache, self)
            self._group_cards.append(card)
            container.add_widget(card)

        self.total_groups = len(groups)
        self._update_batch_bar()

    def _update_batch_bar(self):
        """Aggregate checked state across all GroupCards."""
        total_files = 0
        total_bytes = 0
        for card in self._group_cards:
            cnt, bts = card.get_checked_info()
            total_files += cnt
            total_bytes += bts

        self.total_checked = total_files
        if total_files > 0:
            self.batch_info_text = (
                f'已选 {total_files} 个文件  ({self._format_size(total_bytes)})'
            )
        else:
            self.batch_info_text = '勾选要删除的重复文件'

    def batch_delete_checked(self):
        """Delete all checked files across all groups."""
        # Collect all checked paths
        all_to_delete: list[str] = []
        total_bytes = 0
        for card in self._group_cards:
            for img in card.group.images:
                if img.path in card._checked_paths:
                    all_to_delete.append(img.path)
                    total_bytes += img.file_size

        if not all_to_delete:
            return

        def do_delete(confirmed):
            if confirmed:
                deleted = 0
                errors = 0
                for path in all_to_delete:
                    if _safe_delete_file(path):
                        deleted += 1
                        # Remove from all groups
                        for card in self._group_cards:
                            card.group.images = [i for i in card.group.images if i.path != path]
                            card._checked_paths.discard(path)
                    else:
                        errors += 1

                # Remove resolved groups (<=1 image)
                self._refresh_list()
                msg = f'已删除 {deleted} 张'
                if errors:
                    msg += f', {errors} 张失败'
                popup2 = Popup(
                    title='清理完成',
                    content=Label(text=msg),
                    size_hint=(0.7, 0.3)
                )
                popup2.open()
            popup.dismiss()

        wasted = self._format_size(total_bytes)
        content = BoxLayout(orientation='vertical', spacing='8dp', padding='8dp')
        content.add_widget(Label(
            text=f'删除 {len(all_to_delete)} 张重复图片?\n预计释放: {wasted}',
            halign='center'
        ))
        btns = BoxLayout(size_hint_y=None, height='38dp', spacing='8dp')
        btns.add_widget(Button(text='取消', on_release=lambda x: popup.dismiss()))
        btns.add_widget(Button(
            text='确认删除', on_release=lambda x: do_delete(True),
            background_normal='', background_color=(0.9, 0.3, 0.3, 1),
            color=(1, 1, 1, 1)
        ))
        content.add_widget(btns)
        popup = Popup(title='批量删除', content=content, size_hint=(0.75, 0.4))
        popup.open()

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
