"""Mp3 Player for partial loop and partial save."""
# %% Import
# Standard library imports
import sys
import json
import os.path as osp
import sqlite3
from datetime import datetime

# Third party imports
import qdarkstyle
import qtawesome as qta
from qtpy.QtCore import Qt, QUrl, Signal, Slot, QRect, QSize, QPoint, QTimer
from qtpy.QtGui import QPixmap, QIcon, QPainter
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QHBoxLayout, QPushButton, QProgressBar, QLineEdit,
                            QToolTip, QDial, QAction, QFileDialog, QMessageBox)
from qtpy.QtMultimedia import QMediaPlayer


# enable highdpi scaling
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)  # use highdpi icons


def ms2min_sec(ms: int):
    """Convert milliseconds to 'minutes:seconds'."""
    min_sec = f'{int(ms / 60000):02d}:{int(ms / 1000) % 60:02d}'
    return min_sec


class MusicProgressBar(QProgressBar):
    sig_pb_pos = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.icon_a = qta.icon('fa.caret-down', color="#33bb33")
        self.icon_b = qta.icon('fa.caret-down', color="#bb3333")
        self.icon_size = QSize(30, 30)
        self.pos_loop_a = None
        self.pos_loop_b = None

    def convert_mouse_pos_to_media_pos(self, x_pos: int) -> int:
        """Convert mouse pos to media pos."""
        width = self.frameGeometry().width()
        percent = float(x_pos) / width
        position_ms = int(self.maximum() * percent + 0.5)
        return position_ms

    def convert_media_pos_to_widget_pos(self, media_pos: int) -> int:
        """Convert media pos to widget pos."""
        width = self.frameGeometry().width()
        pos = int(media_pos / self.maximum() * width + 0.5
                  - self.icon_size.width() / 2)
        return pos

    def mouseMoveEvent(self, event):
        """Display a position of media if the mouse is on the progressbar."""
        x_pos = event.pos().x()
        position_ms = self.convert_mouse_pos_to_media_pos(x_pos)
        QToolTip.showText(
            event.globalPos(), ms2min_sec(position_ms))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        """Send the new position of media if the progressbar is clicked."""
        x_pos = event.pos().x()
        position_ms = self.convert_mouse_pos_to_media_pos(x_pos)
        self.sig_pb_pos.emit(position_ms)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        """Draw marker for A/B loop."""
        super().paintEvent(event)
        painter = QPainter(self)
        if self.pos_loop_a:
            pos = self.convert_media_pos_to_widget_pos(self.pos_loop_a)
            self.icon_a.paint(painter, QRect(QPoint(pos, 0), self.icon_size))
        if self.pos_loop_b:
            pos = self.convert_media_pos_to_widget_pos(self.pos_loop_b)
            self.icon_b.paint(painter, QRect(QPoint(pos, 0), self.icon_size))


class MainWindow(QMainWindow):
    max_recent_files = 10

    def __init__(self):
        super().__init__()

        self.setWindowTitle("OkPlayer")

        icon = QIcon()
        icon.addPixmap(QPixmap('ok_64x64.ico'), QIcon.Normal, QIcon.Off)
        self.setWindowIcon(icon)

        self.recent_file_acts = []
        self.init_menu()
        self.now = datetime.now()

        # Setting
        self.setting = {}
        self.load_setting()

        # Status bar
        self.learning_time_ms = 0
        self.learning_time_ms_total = self.setting.get(
            'learning_time_ms_total', 0)
        self.statusBar()
        self.statusBar().showMessage(
            f'Learning time: 00:00 sec'
            f' / total {ms2min_sec(self.learning_time_ms_total)} sec')
        self.timer_learning_time = QTimer(self)
        self.timer_learning_time.timeout.connect(self.update_learning_time)
        self.timer_learning_time.setInterval(1000)

        # Player
        # self.player = QMediaPlayer(None, QMediaPlayer.LowLatency)
        self.player = QMediaPlayer(self)
        self.player.mediaStatusChanged.connect(self.qmp_status_changed)
        self.player.positionChanged.connect(self.qmp_position_changed)
        self.player.setNotifyInterval(50)
        self.player.setVolume(50)
        self.duration_ms = 0
        self.duration_str = ''

        # A/B Loop
        self.pos_loop_a = None
        self.pos_loop_b = None

        # Layout
        self.ico_play = qta.icon("fa.play")
        self.ico_pause = qta.icon("fa.pause")
        layout = QHBoxLayout()
        layout_btn_progress = QVBoxLayout()
        layout_mp3_btns = QHBoxLayout()
        self.btn_rewind = QPushButton(qta.icon("fa.backward"), '', self)
        self.btn_rewind.clicked.connect(self.rewind)
        self.btn_play = QPushButton(self.ico_play, '', self)
        self.btn_play.clicked.connect(self.play)
        self.btn_fastforward = QPushButton(qta.icon("fa.forward"), '', self)
        self.btn_fastforward.clicked.connect(self.fastforward)

        layout_mp3_btns.addWidget(self.btn_rewind)
        layout_mp3_btns.addWidget(self.btn_play)
        layout_mp3_btns.addWidget(self.btn_fastforward)

        layout_progress = QHBoxLayout()
        self.progressbar = MusicProgressBar(self)
        self.progressbar.sig_pb_pos.connect(self.set_media_position)
        self.elapsed_time = QLineEdit(f"00:00 / 00:00", self)
        self.elapsed_time.setReadOnly(True)
        self.elapsed_time.setAlignment(Qt.AlignHCenter)

        layout_progress.addWidget(self.progressbar)
        layout_progress.addWidget(self.elapsed_time)

        layout_btn_progress.addLayout(layout_mp3_btns)
        layout_btn_progress.addLayout(layout_progress)

        # Volume
        self.qdial_volume = QDial(self)
        self.qdial_volume.setWrapping(False)
        self.qdial_volume.setNotchesVisible(True)
        self.qdial_volume.setMinimum(0)
        self.qdial_volume.setMaximum(100)
        self.qdial_volume.setValue(self.player.volume())
        self.qdial_volume.valueChanged.connect(self.qdial_changed)

        layout.addLayout(layout_btn_progress)
        layout.addWidget(self.qdial_volume)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        # Auto Play
        self.update_recent_file_action()
        path = self.setting.get('LastPlayedPath', '')
        if osp.isfile(path):
            self.player.setMedia(QUrl.fromLocalFile(path))

    def init_menu(self):
        """Init menu."""
        color_icon = '#87939A'
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(False)  # Don't use mac native menu bar

        # File
        file_menu = menu_bar.addMenu('&File')

        # Open
        open_action = QAction(
            qta.icon("ei.folder-open", color=color_icon), '&Open', self)
        open_action.setShortcut('Ctrl+O')
        open_action.setStatusTip('Open mp3')
        open_action.triggered.connect(self.open_mp3)
        file_menu.addAction(open_action)
        file_menu.addSeparator()

        # Recent Files
        for i in range(MainWindow.max_recent_files):
            self.recent_file_acts.append(
                QAction(self, visible=False, triggered=self.load_recent_mp3))
        for i in range(MainWindow.max_recent_files):
            file_menu.addAction(self.recent_file_acts[i])

        file_menu.addSeparator()

        # Exit
        exit_action = QAction(
            qta.icon("mdi.exit-run", color=color_icon), '&Exit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.setStatusTip('Exit App')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help
        help_menu = menu_bar.addMenu('&Help')
        about_action = QAction("&About", self,
                               statusTip="Show the application's About box",
                               triggered=self.about)
        help_menu.addAction(about_action)

    def about(self):
        """Show messagebox for about."""
        QMessageBox.about(
            self, "About mp3 player a/b loop",
            "The Mp3 player a/b loop is made by <b>ok97465</b>")

    def update_recent_file_action(self):
        """Update recent file action."""
        files = self.setting.get('recent_files', [])

        num_recent_files = min(len(files), MainWindow.max_recent_files)

        for i in range(num_recent_files):
            text = osp.splitext(osp.basename(files[i]))[0]
            self.recent_file_acts[i].setText(text)
            self.recent_file_acts[i].setData(files[i])
            self.recent_file_acts[i].setVisible(True)

        for j in range(num_recent_files, MainWindow.max_recent_files):
            self.recent_file_acts[j].setVisible(False)

    def open_mp3(self):
        """Open mp3."""
        fname = QFileDialog.getOpenFileName(
            self, 'Open mp3 file', '/home/ok97465', filter='*.mp3')
        self.load_mp3(fname[0])

    def load_mp3(self, path: str):
        """Load mp3"""
        if path.startswith('file://'):
            path = path[7:]
        if not osp.isfile(path):
            return
        self.save_current_media_info()
        self.player.setMedia(QUrl.fromLocalFile(path))

    def load_recent_mp3(self):
        """Load recent mp3."""
        action = self.sender()
        if action:
            self.load_mp3(action.data())

    def load_setting(self):
        """Load setting file."""
        try:
            with open('setting.json', 'r') as fp:
                self.setting = json.load(fp)
        except FileNotFoundError:
            pass

    def keyPressEvent(self, event):
        key = event.key()
        shift = event.modifiers() & Qt.ShiftModifier
        if shift:
            if key == Qt.Key_O:
                self.adjust_ab_loop(-100)
        else:
            if key == Qt.Key_H:
                self.rewind(ms=5000)
            elif key == Qt.Key_L:
                self.fastforward(ms=5000)
            if key == Qt.Key_J:
                self.rewind(ms=1000 * 60)
            elif key == Qt.Key_K:
                self.fastforward(ms=1000 * 60)
            elif key == Qt.Key_Up:
                self.control_volume(5)
            elif key == Qt.Key_Down:
                self.control_volume(-5)
            elif key == Qt.Key_I:
                self.set_ab_loop()
            elif key == Qt.Key_O:
                self.adjust_ab_loop(500)
            elif key == Qt.Key_Space:
                self.play()

        super().keyPressEvent(event)

    def set_ab_loop(self):
        """Set A/B loop."""
        if self.pos_loop_b:
            self.pos_loop_b = None
            self.pos_loop_a = None
        elif self.pos_loop_a:
            self.pos_loop_b = self.player.position()
            self.player.setPosition(self.pos_loop_a)
        else:
            self.pos_loop_a = self.player.position()

        self.progressbar.pos_loop_a = self.pos_loop_a
        self.progressbar.pos_loop_b = self.pos_loop_b
        self.progressbar.repaint()

    def adjust_ab_loop(self, offset_ms):
        """Adjust A/B loop."""
        if self.pos_loop_b:
            self.pos_loop_b += offset_ms
            self.pos_loop_a += offset_ms

    def play(self):
        """Play mp3."""
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setIcon(self.ico_play)
            self.timer_learning_time.stop()
        else:
            self.player.play()
            self.btn_play.setIcon(self.ico_pause)
            self.timer_learning_time.start()

    def control_volume(self, step: int):
        """Control volume."""
        volume = self.player.volume()
        if step < 0:
            new_volume = max([0, volume + step])
        else:
            new_volume = min([100, volume + step])
        self.qdial_volume.setValue(new_volume)

    def navigate_media(self, ms: int):
        """Navigate the position of media."""
        position_ms = self.player.position()
        if ms < 0:
            new_position_ms = max([0, position_ms + ms])
        else:
            new_position_ms = min([self.duration_ms, position_ms + ms])
        self.player.setPosition(new_position_ms)

    def rewind(self, ms: int = 5000):
        """Re-wind media of QMediaPlayer."""
        self.navigate_media(ms * -1)

    def fastforward(self, ms: int = 5000):
        """fastfoward media of QMediaPlayer."""
        self.navigate_media(ms)

    def qmp_status_changed(self):
        """Handle status of QMediaPlayer if the status is changed."""
        status = self.player.mediaStatus()
        if status == QMediaPlayer.LoadedMedia:
            duration_ms = self.player.duration()
            self.duration_ms = duration_ms
            self.duration_str = ms2min_sec(duration_ms)
            self.elapsed_time.setText(f'00:00 / {self.duration_str}')
            self.progressbar.setMaximum(duration_ms)
            self.player.play()

            # read previous position
            path = self.player.currentMedia().resources()[0].url().url()
            position = self.setting.get(path, 0)
            self.player.setPosition(position)

            # update recent files
            files = self.setting.get("recent_files", [])
            try:
                files.remove(path)
            except ValueError:
                pass
            files.insert(0, path)
            del files[MainWindow.max_recent_files:]
            self.setting['recent_files'] = files
            self.update_recent_file_action()

        # Player state
        state = self.player.state()
        if state in [QMediaPlayer.PausedState, QMediaPlayer.StoppedState]:
            self.btn_play.setIcon(self.ico_play)
            self.timer_learning_time.stop()
        elif state == QMediaPlayer.PlayingState:
            self.btn_play.setIcon(self.ico_pause)
            self.timer_learning_time.start()

    def qmp_position_changed(self, position_ms: int):
        """Handle position of qmedia if the position is changed."""
        if self.pos_loop_b:
            if ((position_ms == self.duration_ms)
                    or (self.pos_loop_b < position_ms)):
                self.player.setPosition(self.pos_loop_a)
        self.progressbar.setValue(position_ms)
        self.elapsed_time.setText(
            f'{ms2min_sec(position_ms)} / {self.duration_str}')

    def qdial_changed(self, pos: int):
        """Handle Qdial position."""
        self.player.setVolume(pos)

    @Slot(int)
    def set_media_position(self, position_ms: int):
        """Set the position of Qmedia."""
        self.player.setPosition(position_ms)

    def save_current_media_info(self):
        """Save current media info to setting file."""
        res = self.player.currentMedia().resources()
        if not res:
            return
        path = res[0].url().url()
        position = self.player.position()
        self.setting[path] = position

        if path.startswith('file://'):
            path = path[7:]
        self.setting['LastPlayedPath'] = path

        self.player.stop()
        self.timer_learning_time.stop()

    def update_learning_time(self):
        """Update learning time."""
        self.learning_time_ms += 1000
        self.learning_time_ms_total += 1000
        self.statusBar().showMessage(
            f'Learning time : {ms2min_sec(self.learning_time_ms)} sec'
            f' / total : {ms2min_sec(self.learning_time_ms_total)} sec')

    def closeEvent(self, event):
        """Save setting."""
        self.save_current_media_info()
        self.setting['learning_time_ms_total'] = self.learning_time_ms_total
        setting_json = json.dumps(self.setting)

        with open('setting.json', 'w') as fp:
            fp.write(setting_json)

        now = self.now
        cur = sqlite3.connect("history.db")
        cur.execute(
            'CREATE TABLE IF NOT EXISTS LearningTimeData('
            'DayOfWeek INTEGER, '
            'month  INTEGER, '
            'day INTEGER,  '
            'timestamp REAL, '
            'LearningTime_ms INTEGER)')
        cur.execute(
            "insert into LearningTimeData Values (?,?,?,?,?)",
            (now.weekday(), now.month, now.day, now.timestamp(),
             self.learning_time_ms))
        cur.commit()
        cur.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    style_sheet = qdarkstyle.load_stylesheet_pyside2()
    app.setStyleSheet(style_sheet)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
