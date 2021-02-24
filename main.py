"""Mp3 Player for partial loop and partial save."""
# %% Import
# Standard library imports
import sys
import json

# Third party imports
import qdarkstyle
import qtawesome as qta
from qtpy.QtCore import Qt, QUrl, Signal, Slot, QRect, QSize, QPoint
from qtpy.QtGui import QPixmap, QIcon, QPainter
from qtpy.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                            QHBoxLayout, QPushButton, QProgressBar, QLineEdit,
                            QToolTip, QDial)
from qtpy.QtMultimedia import QMediaPlayer


# enable highdpi scaling
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)  # use highdpi icons


def ms2min_sec(ms: int):
    """Convert milliseconds to 'minutes:seconds'."""
    min_sec = f'{int(ms / 60000)}:{int(ms / 1000) % 60:02d}'
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
    def __init__(self):
        super().__init__()

        self.setWindowTitle("OkPlayer")

        icon = QIcon()
        icon.addPixmap(QPixmap('ok_64x64.ico'), QIcon.Normal, QIcon.Off)
        self.setWindowIcon(icon)

        self.config = {}
        self.load_config()

        # Player
        path = "/home/ok97465/a.mp3"
        self.play_latency = 100
        self.player = QMediaPlayer(None, QMediaPlayer.LowLatency)
        self.player.setMedia(QUrl.fromLocalFile(path))
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
        layout = QHBoxLayout()
        layout_btn_progress = QVBoxLayout()
        layout_mp3_btns = QHBoxLayout()
        self.btn_rewind = QPushButton(qta.icon("fa.backward"), '', self)
        self.btn_rewind.clicked.connect(self.rewind)
        self.btn_play = QPushButton(qta.icon("fa.play"), '', self)
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

    def load_config(self):
        """Load config file."""
        try:
            with open('config.json', 'r') as fp:
                self.config = json.load(fp)
        except FileNotFoundError:
            pass

    def keyPressEvent(self, event):
        key = event.key()
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

        super().keyPressEvent(event)

    def set_ab_loop(self):
        """Set A/B loop."""
        if self.pos_loop_b:
            self.pos_loop_b = None
            self.pos_loop_a = None
        elif self.pos_loop_a:
            self.pos_loop_b = self.player.position() + self.play_latency
            self.player.setPosition(self.pos_loop_a)
        else:
            self.pos_loop_a = self.player.position() + self.play_latency

        self.progressbar.pos_loop_a = self.pos_loop_a
        self.progressbar.pos_loop_b = self.pos_loop_b
        self.progressbar.repaint()

    def play(self):
        """Play mp3."""
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

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
        if self.player.mediaStatus() == QMediaPlayer.LoadedMedia:
            duration_ms = self.player.duration()
            self.duration_ms = duration_ms
            self.duration_str = ms2min_sec(duration_ms)
            self.elapsed_time.setText(f'00:00 / {self.duration_str}')
            self.progressbar.setMaximum(duration_ms)
            self.player.play()

            # read previous position
            path = self.player.currentMedia().resources()[0].url().url()
            position = self.config.get(path, 0)
            self.player.setPosition(position)

    def qmp_position_changed(self, position_ms: int):
        """Handle position of qmedia if the position is changed."""
        if self.pos_loop_b:
            if ((position_ms == self.duration_ms)
                    or (self.pos_loop_b < position_ms)):
                self.player.setPosition(self.pos_loop_a)
        else:
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

    def closeEvent(self, event):
        """Save configuration."""
        path = self.player.currentMedia().resources()[0].url().url()
        position = self.player.position()

        self.config[path] = position
        config_json = json.dumps(self.config)

        with open('config.json', 'w') as fp:
            fp.write(config_json)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    style_sheet = qdarkstyle.load_stylesheet_pyside2()
    app.setStyleSheet(style_sheet)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
