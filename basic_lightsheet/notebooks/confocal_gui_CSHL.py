from nidaqmx import Task
from nidaqmx.constants import AcquisitionType, Edge
from nidaqmx.stream_readers import AnalogMultiChannelReader
from nidaqmx.stream_writers import AnalogMultiChannelWriter

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QFileDialog
from PyQt5 import QtWidgets, QtCore
from superqt import QLabeledDoubleRangeSlider, QLabeledDoubleSlider, QLabeledSlider
import pyqtgraph as pg

import numpy as np
import sys
from skimage import io

class ConfocalMicroscopy(QWidget):
    def __init__(self, samp_rate=10000, amp=3, num_px=100):
        super().__init__()

        self.dev = "Dev1"
        self.ao_chx = "ao0"# fast galvo
        self.ao_chy = "ao3" # slow galvo
        self.ai_ch = "ai7"

        self.rate = samp_rate # samples per channel per second
        self.amp = amp # scanning range in volt
        self.x_offset = 0
        self.y_offset = 0

        self.num_px = num_px # the number of pixels for both x and y
        self.total_px = self.num_px * self.num_px # aspect ratio = 1

        self.write_signal = self.waveform()
        self.read_buffer = np.empty((1, self.total_px))
        self.buf_size = 5 # times bigger than one frame

        self.running = False
        self.first_img = True

        self.last_frame = []

        self.set_gui()

    def set_gui(self):
        self.start_button = QPushButton("Start acquisition")
        self.start_button.clicked.connect(self.toggle)

        self.save_button = QPushButton("Save last acquisition")
        self.save_button.clicked.connect(self.save_acquisition)

        self.x_amp = QLabeledDoubleSlider(QtCore.Qt.Horizontal)
        self.x_amp.setRange(0.1, 6)
        self.x_amp.setValue(3)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.x_amp.valueChanged.connect(self.update_acq_params)

        # self.srate_slider = QLabeledDoubleSlider(QtCore.Qt.Horizontal)
        # self.srate_slider.setRange(self.total_px, self.total_px*10)
        # self.srate_slider.setSingleStep(self.total_px)
        # self.srate_slider.setValue(3)
        # self.setFocusPolicy(QtCore.Qt.NoFocus)
        # self.srate_slider.valueChanged.connect(self.update_acq_params)

        self.viewer = pg.ImageView()
        self.viewer.getHistogramWidget().setHistogramRange(0, 1)
        self.viewer.ui.roiBtn.hide()
        self.viewer.ui.menuBtn.hide()

        layout = QVBoxLayout()

        layout2 = QHBoxLayout()
        layout2.addWidget(self.start_button)
        layout2.addWidget(self.save_button)
        layout.addLayout(layout2)

        layout3 = QHBoxLayout()
        layout3.addWidget(QLabel("Zoom (V)"))
        layout3.addWidget(self.x_amp)
        layout.addLayout(layout3)

        layout4 = QHBoxLayout()
        layout4.addWidget(QLabel("Sampling rate (samples/s)"))
        layout4.addWidget(QLabel("{:.3f}fps".format(self.rate/self.total_px)))
        # layout4.addWidget(self.srate_slider)
        layout.addLayout(layout4)

        layout.addWidget(self.viewer)

        self.setLayout(layout)

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def save_acquisition(self):
        if self.last_frame.any():
            filename = QFileDialog.getSaveFileName(filter="Tif files (*.tif)")[0]
            io.imsave(filename, np.stack(self.last_frame))
            print(f"Saved last acq as {filename}")
        else:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Information)
            msg.setText(f"Start acquisition before trying to save data!")
            msg.setWindowTitle("No data acquired")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()

    def update_acq_params(self):
        self.amp = self.x_amp.value()
        # self.rate = self.srate_slider.value()

        self.write_signal = self.waveform()

    def start(self):
        self.start_button.setText("Stop acquisition")
        self.start_button.setStyleSheet("background-color: yellow")
        self.running = True

        self.set_tasks()
        self.read_task.start()
        self.write_task.start()

    def stop(self):
        self.start_button.setText("Start acquisition")
        self.start_button.setStyleSheet("")
        self.running = False
        
        self.write_task.stop()
        self.write_task.close()
        self.read_task.stop()
        self.read_task.close()

        self.last_frame = self.reconstruct_image(self.read_buffer)

    def waveform(self):
        line = np.linspace(-1, 1, self.num_px)
        x = np.tile(np.r_[line, line[::-1]], self.num_px // 2)
        if self.num_px % 2:
            x = np.r_[x, line]
        y = np.repeat(line, self.num_px)
        return np.stack([x+self.x_offset, y+self.y_offset]) * self.amp
    
    def set_tasks(self):
        self.write_task = Task()
        self.write_task.ao_channels.add_ao_voltage_chan(f"{self.dev}/ao0", min_val=-8, max_val=8)
        self.write_task.ao_channels.add_ao_voltage_chan(f"{self.dev}/ao3", min_val=-8, max_val=8)
        self.write_task.timing.cfg_samp_clk_timing(
            rate=self.rate,
            source="OnboardClock",
            active_edge=Edge.RISING,
            sample_mode=AcquisitionType.CONTINUOUS,
        )
        self.write_task.out_stream.output_buf_size = self.total_px * 2 * self.buf_size
        self.write_task.register_every_n_samples_transferred_from_buffer_event(self.total_px, self.write_callback)
        self.writer = AnalogMultiChannelWriter(self.write_task.out_stream)
        for _ in range(self.buf_size):
            self.writer.write_many_sample(self.write_signal)

        self.read_task = Task()
        self.read_task.ai_channels.add_ai_voltage_chan(f"{self.dev}/ai7", min_val=-5, max_val=5)
        self.read_task.timing.cfg_samp_clk_timing(
            rate=self.rate,
            source="OnboardClock",
            active_edge=Edge.RISING,
            sample_mode=AcquisitionType.CONTINUOUS,
        )
        self.read_task.in_stream.input_buf_size = self.total_px * self.buf_size
        self.read_task.triggers.start_trigger.cfg_dig_edge_start_trig(f"/{self.dev}/ao/StartTrigger", Edge.RISING)
        self.read_task.register_every_n_samples_acquired_into_buffer_event(self.total_px, self.read_callback)
        self.reader = AnalogMultiChannelReader(self.read_task.in_stream)

    def write_callback(self, task_handle, every_n_samples_event_type, number_of_samples, callback_data):
        self.writer.write_many_sample(self.write_signal, timeout=1)
        return 0
    
    def read_callback(self, task_handle, every_n_samples_event_type, number_of_samples, callback_data):
        self.reader.read_many_sample(self.read_buffer, number_of_samples, timeout=1)
        self.update(self.reconstruct_image(self.read_buffer))
        return 0

    def reconstruct_image(self, read_buffer):
        hardcoded_shift = -3
        img = read_buffer.copy()
        img = np.roll(img, hardcoded_shift).reshape(self.num_px, self.num_px)
        img[0::2, :] = img[0::2, ::-1]
        img = np.flipud(img)
        return img
        
    def update(self, img):

        self.viewer.setImage(
            img.T,
            autoLevels=self.first_img,
            autoHistogramRange=False,
        )
        self.first_img = False


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main = ConfocalMicroscopy()
    main.show()
    sys.exit(app.exec_())
