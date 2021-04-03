"""Mp3 Player for partial loop and partial save."""
# %% Import
# Standard library imports
import sys
import json
import os.path as osp
import sqlite3
import io
from datetime import datetime
from bisect import bisect_right

# Third party imports
import qdarkstyle
import qtawesome as qta
import webvtt
from pydub import AudioSegment
from qtpy.QtCore import (Qt, Signal, Slot, QRect, QSize, QPoint, QTimer,
                         QIODevice, QBuffer)
from qtpy.QtGui import QPixmap, QIcon, QPainter
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QHBoxLayout, QPushButton, QProgressBar, QLineEdit,
                            QToolTip, QDial, QAction, QFileDialog, QMessageBox,
                            QLabel, QFrame, QPlainTextEdit)
from qtpy.QtMultimedia import QMediaPlayer, QMediaContent


# enable highdpi scaling
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)  # use highdpi icons


def ms2min_sec(ms: int):
    """Convert milliseconds to 'minutes:seconds'."""
    min_sec = f'{int(ms / 60000):02d}:{int(ms / 1000) % 60:02d}'
    return min_sec


class VLine(QFrame):
    # a simple VLine, like the one you get from designer
    def __init__(self):
        super(VLine, self).__init__()
        self.setFrameShape(QFrame.VLine)


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
            self.icon_b.paint(painter, QRect(QPoint(pos, -3), self.icon_size))


class LyricsDisplay(QPlainTextEdit):
    """Display lyrics."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFixedHeight(50)

        self.start_time_lyrics = []
        self.lyrics = []

    def read_vtt(self, path: str):
        self.start_time_lyrics = []
        self.lyrics = []

        if osp.isfile(path) is False:
            return

        for lyrics_info in webvtt.read(path):
            hour = int(lyrics_info.start[:2])
            min_ = int(lyrics_info.start[3:5])
            millisec = int(float(lyrics_info.start[6:]) * 1000)
            time_ms = 1000 * (hour * 60 * 60 + min_ * 60) + millisec
            self.start_time_lyrics.append(time_ms)
            self.lyrics.append(lyrics_info.text)

        self.start_time_lyrics = self.start_time_lyrics[::2]
        self.lyrics = self.lyrics[::2]

        self.setPlainText(self.lyrics[0])

    def update_media_pos(self, pos_ms):
        if not self.lyrics:
            return
        idx = bisect_right(self.start_time_lyrics, pos_ms) - 1
        idx = max([idx, 0])
        self.setPlainText(self.lyrics[idx])


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
        self.status_bar = self.statusBar()
        self.label_learning_time = QLabel(self)
        self.label_learning_time.setAlignment(Qt.AlignRight)

        self.status_bar.addPermanentWidget(self.label_learning_time)
        self.label_learning_time.setText(
            f'Learning time: 00:00'
            f' / total {ms2min_sec(self.learning_time_ms_total)}')

        # Timer for learning time
        self.timer_learning_time = QTimer(self)
        self.timer_learning_time.timeout.connect(self.update_learning_time)
        self.timer_learning_time.setInterval(1000)

        # Player
        self.player = QMediaPlayer(self)
        self.player.mediaStatusChanged.connect(self.qmp_status_changed)
        self.player.positionChanged.connect(self.qmp_position_changed)
        self.player.setNotifyInterval(50)
        self.player.setVolume(50)
        self.player_buf = QBuffer()
        self.path_media = ''
        self.mp3_data = None
        self.duration_ms = 0
        self.duration_str = ''

        # A/B Loop
        self.pos_loop_a = None
        self.pos_loop_b = None

        # Layout
        self.label_mp3 = QLabel("No mp3", self)

        self.ico_play = qta.icon("fa.play")
        self.ico_pause = qta.icon("fa.pause")

        layout = QVBoxLayout()
        layout_volume = QHBoxLayout()
        layout_btn_progress = QVBoxLayout()
        layout_mp3_btns = QHBoxLayout()
        self.btn_rewind = QPushButton(qta.icon("fa.backward"), '', self)
        self.btn_rewind.clicked.connect(self.rewind)
        self.btn_play = QPushButton(self.ico_play, '', self)
        self.btn_play.clicked.connect(self.play)
        self.btn_fastforward = QPushButton(qta.icon("fa.forward"), '', self)
        self.btn_fastforward.clicked.connect(self.fastforward)

        self.btn_rewind.setFocusPolicy(Qt.NoFocus)
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_fastforward.setFocusPolicy(Qt.NoFocus)

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

        layout_btn_progress.addWidget(self.label_mp3)
        layout_btn_progress.addLayout(layout_mp3_btns)
        layout_btn_progress.addLayout(layout_progress)

        # Volume
        self.qdial_volume = QDial(self)
        self.qdial_volume.setMinimumWidth(110)
        self.qdial_volume.setWrapping(False)
        self.qdial_volume.setNotchesVisible(True)
        self.qdial_volume.setMinimum(0)
        self.qdial_volume.setMaximum(100)
        self.qdial_volume.setValue(self.player.volume())
        self.qdial_volume.valueChanged.connect(self.qdial_changed)

        layout_volume.addLayout(layout_btn_progress)
        layout_volume.addWidget(self.qdial_volume)

        # Lyrics
        self.display_lyrics = LyricsDisplay(self)
        layout.addLayout(layout_volume)
        layout.addWidget(self.display_lyrics)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        # Auto Play
        self.update_recent_file_action()
        path = self.setting.get('LastPlayedPath', '')
        if osp.isfile(path):
            self.load_mp3(path)

        self.setFocus()

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
        self.stop()
        fname = QFileDialog.getOpenFileName(
            self, 'Open mp3 file', '/home/ok97465', filter='*.mp3')
        self.load_mp3(fname[0])

    def load_mp3(self, path: str):
        """Load mp3"""
        if not osp.isfile(path):
            return
        self.path_media = path

        path_lyrics = path[:-3] + 'vtt'
        self.display_lyrics.read_vtt(path_lyrics)

        fp = io.BytesIO()
        self.mp3_data = AudioSegment.from_file(path)
        self.mp3_data.export(fp, format='wav')
        self.player_buf.setData(fp.getvalue())
        self.player_buf.open(QIODevice.ReadOnly)
        self.player.setMedia(QMediaContent(), self.player_buf)

    def load_recent_mp3(self):
        """Load recent mp3."""
        action = self.sender()
        if action:
            self.stop()
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
            if key in [Qt.Key_H, Qt.Key_Left, Qt.Key_A]:
                self.rewind(ms=5000)
            elif key in [Qt.Key_L, Qt.Key_Right, Qt.Key_D]:
                self.fastforward(ms=5000)
            elif key in [Qt.Key_J]:
                self.rewind(ms=1000 * 38)
            elif key in [Qt.Key_K, Qt.Key_F]:
                self.fastforward(ms=1000 * 38)
            elif key == Qt.Key_Up:
                self.control_volume(5)
            elif key == Qt.Key_Down:
                self.control_volume(-5)
            elif key in [Qt.Key_I, Qt.Key_W]:
                self.set_ab_loop()
            elif key == Qt.Key_O:
                self.adjust_ab_loop(500)
            elif key == Qt.Key_Space:
                self.play()
            elif key == Qt.Key_S:
                self.save_ab_loop()

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

    def save_ab_loop(self):
        """Save A/B loop"""
        if self.pos_loop_b is None:
            return

        is_playing = False
        if self.player.state() == QMediaPlayer.PlayingState:
            is_playing = True

        if is_playing:
            self.player.pause()
        path_new = (self.path_media[:-4]
                    + f"{self.pos_loop_a}_{self.pos_loop_b}"
                    + self.path_media[-4:])
        seg = self.mp3_data[self.pos_loop_a:self.pos_loop_b]
        seg.export(path_new, format='mp3')

        if is_playing:
            self.player.play()

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

    def stop(self):
        """Stop."""
        self.save_current_media_info()
        self.player.stop()
        self.player_buf.close()
        self.path_media = ''
        self.pos_loop_b = None
        self.pos_loop_a = None
        self.timer_learning_time.stop()
        self.label_mp3.setText("No mp3")
        self.btn_play.setIcon(self.ico_play)

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
        if status == QMediaPlayer.LoadedMedia and self.path_media:
            duration_ms = self.player.duration()
            self.duration_ms = duration_ms
            self.duration_str = ms2min_sec(duration_ms)
            self.elapsed_time.setText(f'00:00 / {self.duration_str}')
            self.progressbar.setMaximum(duration_ms)
            mp3_basename = osp.splitext(osp.basename(self.path_media))[0]
            self.label_mp3.setText(mp3_basename)
            self.player.play()

            # read previous position
            path = self.path_media
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
        self.display_lyrics.update_media_pos(position_ms)

    def qdial_changed(self, pos: int):
        """Handle Qdial position."""
        self.player.setVolume(pos)

    @Slot(int)
    def set_media_position(self, position_ms: int):
        """Set the position of Qmedia."""
        self.player.setPosition(position_ms)

    def save_current_media_info(self):
        """Save current media info to setting file."""
        if not osp.isfile(self.path_media):
            return
        if self.path_media:
            position = self.player.position()
            self.setting[self.path_media] = position
            self.setting['LastPlayedPath'] = self.path_media

    def update_learning_time(self):
        """Update learning time."""
        self.learning_time_ms += 1000
        self.learning_time_ms_total += 1000
        self.label_learning_time.setText(
            f'Learning time : {ms2min_sec(self.learning_time_ms)}'
            f' / total : {ms2min_sec(self.learning_time_ms_total)}')

    def closeEvent(self, event):
        """Save setting."""
        self.stop()
        self.setting['learning_time_ms_total'] = self.learning_time_ms_total

        with open('setting.json', 'w') as fp:
            json.dump(self.setting, fp, indent=2)

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
