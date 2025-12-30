from nidaqmx import Task
from nidaqmx.constants import AcquisitionType, Edge
from nidaqmx.stream_readers import AnalogMultiChannelReader
from nidaqmx.stream_writers import AnalogMultiChannelWriter
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton
import pyqtgraph as pg
import numpy as np
import sys


class ConfocalMicroscopy(QWidget):
    def __init__(self):
        super().__init__()
        self.dev = "Dev1"
        self.rate = 40000
        self.amp = 4
        self.num_px = 200
        self.total_px = self.num_px * self.num_px

        self.write_signal = self.waveform()
        self.read_buffer = np.empty((1, self.total_px))
        self.buf_size = 3
        self.running = False
        self.first_img = True

        self.set_gui()

    def set_gui(self):
        self.button = QPushButton("start")
        self.button.clicked.connect(self.toggle)
        
        self.viewer = pg.ImageView()
        self.viewer.ui.roiBtn.hide()
        self.viewer.ui.menuBtn.hide()

        layout = QVBoxLayout()
        layout.addWidget(self.button)
        layout.addWidget(self.viewer)
        self.setLayout(layout)

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.button.setText("stop")
        self.button.setStyleSheet("background-color: yellow")
        self.running = True

        self.set_tasks()
        self.read_task.start()
        self.write_task.start()

    def stop(self):
        self.button.setText("start")
        self.button.setStyleSheet("")
        self.running = False
        
        self.write_task.stop()
        self.write_task.close()
        self.read_task.stop()
        self.read_task.close()

    def waveform(self):
        line = np.linspace(-1, 1, self.num_px)
        x = np.tile(np.r_[line, line[::-1]], self.num_px // 2)
        if self.num_px % 2:
            x = np.r_[x, line]
        y = np.repeat(np.linspace(-1, 1, self.num_px), self.num_px)
        return np.stack([x, y]) * self.amp
    
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
        img = read_buffer.copy().reshape(self.num_px, self.num_px)
        img[1::2, :] = img[1::2, ::-1]
        return img
        
    def update(self, img):
        self.viewer.setImage(
            img.T,
            autoLevels=self.first_img,
            autoHistogramRange=self.first_img,
        )
        self.first_img = False


if __name__ == '__main__':
    app = QApplication(sys.argv)
    main = ConfocalMicroscopy()
    main.show()
    sys.exit(app.exec_())
