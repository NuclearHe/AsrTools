import logging
import math
import os
import uuid

from pathlib import Path
import platform
import subprocess
import sys
import webbrowser

from PIL import Image, ImageFilter

# FIX: 修复中文路径报错 https://github.com/WEIFENG2333/AsrTools/issues/18  设置QT_QPA_PLATFORM_PLUGIN_PATH 
plugin_path = os.path.join(sys.prefix, 'Lib', 'site-packages', 'PyQt5', 'Qt5', 'plugins')
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path

from PyQt5.QtCore import Qt, QRunnable, QThreadPool, QObject, pyqtSignal as Signal, pyqtSlot as Slot, QSize, QThread, \
    pyqtSignal
from PyQt5.QtGui import QCursor, QColor, QFont
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
                             QTableWidgetItem, QHeaderView, QSizePolicy, QCheckBox, QFrame)
from qfluentwidgets import (ComboBox, PushButton, LineEdit, TableWidget, FluentIcon,
                            Action, RoundMenu, InfoBar, InfoBarPosition,
                            FluentWindow, BodyLabel, MessageBox, SpinBox)

from bk_asr.BcutASR import BcutASR
from bk_asr.JianYingASR import JianYingASR
from bk_asr.KuaiShouASR import KuaiShouASR

# 设置日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


class WorkerSignals(QObject):
    finished = Signal(str, str)
    errno = Signal(str, str)

def img_resize(img_path:str,tar_width:int,tar_height:int,radius:int=15,padding:float=0.1):
    with Image.open(img_path) as image:
        if image.width/image.height<=tar_width/tar_height:
            back_size = (tar_width, int(tar_width * image.height / image.width))
            fore_size = (int(tar_height*(1.0-padding*2) * image.width / image.height), int(tar_height*(1.0-padding*2)))
            back_image = image.resize(back_size).filter(ImageFilter.GaussianBlur(radius=radius))
            fore_image = image.resize(fore_size).convert("RGBA")
            back_image = back_image.crop(
                (0, (back_image.height - tar_height) / 2, tar_width, (back_image.height - tar_height) / 2 + tar_height))
        else:
            back_size = (int(tar_height * image.width / image.height), tar_height)
            if int(tar_width * image.height / image.width)>tar_height*(1.0-padding*2):
                fore_size = (int(tar_width * tar_height*(1.0-padding*2)/(tar_width * image.height / image.width)), int(tar_height*(1.0-padding*2)))
            else:
                fore_size = (int(tar_width), int(tar_width* image.height / image.width))
            back_image = image.resize(back_size).filter(ImageFilter.GaussianBlur(radius=radius))
            fore_image = image.resize(fore_size).convert("RGBA")
            back_image = back_image.crop(
                ((back_image.width - tar_width) / 2,0 ,(back_image.width - tar_width) / 2 + tar_width,tar_height))
        fill_x = int((tar_width - fore_image.width) / 2)
        fill_y = int((tar_height - fore_image.height) / 2)
        back_image.paste(fore_image, ( fill_x,fill_y), fore_image)

        resize_file = img_path.rsplit(".", 1)[0] + '_resize_rad_' + str(radius)+"_"+str(uuid.uuid4())[:8] + ".png"
        back_image.save(resize_file)
        return resize_file

class ASRWorker(QRunnable):
    """ASR处理工作线程"""
    def __init__(self, file_path, asr_engine, export_format,ui_self):
        super().__init__()
        self.file_path = file_path
        self.asr_engine = asr_engine
        self.export_format = export_format
        self.signals = WorkerSignals()
        self.ui_self=ui_self
        self.audio_path = None

    @Slot()
    def run(self):
        try:
            use_cache = True
            
            # 检查文件类型,如果不是音频则转换
            logging.info("[+]正在进ffmpeg转换")
            audio_exts = ['.mp3', '.wav']
            temp_audio="default"
            if not any(self.file_path.lower().endswith(ext) for ext in audio_exts):
                temp_audio = self.file_path.rsplit(".", 1)[0] +"_"+str(uuid.uuid4())[:8]+ ".mp3"
                if not video2audio(self.file_path, temp_audio):
                    raise Exception("音频转换失败，确保安装ffmpeg")
                self.audio_path = temp_audio
            else:
                self.audio_path = self.file_path
            
            # 根据选择的 ASR 引擎实例化相应的类
            if self.asr_engine == 'B 接口':
                asr = BcutASR(self.audio_path, use_cache=use_cache)
            elif self.asr_engine == 'J 接口':
                asr = JianYingASR(self.audio_path, use_cache=use_cache)
            elif self.asr_engine == 'K 接口':
                asr = KuaiShouASR(self.audio_path, use_cache=use_cache)
            elif self.asr_engine == 'Whisper':
                # from bk_asr.WhisperASR import WhisperASR
                # asr = WhisperASR(self.file_path, use_cache=use_cache)
                raise NotImplementedError("WhisperASR 暂未实现")
            else:
                raise ValueError(f"未知的 ASR 引擎: {self.asr_engine}")

            logging.info(f"开始处理文件: {self.file_path} 使用引擎: {self.asr_engine}")
            result = asr.run()
            
            # 根据导出格式选择转换方法
            save_ext = self.export_format.lower()
            result_text=""
            if save_ext == 'srt':
                result_text = result.to_srt()
            elif save_ext == 'ass':
                result_text = result.to_ass()
            elif save_ext == 'txt':
                result_text = result.to_txt()
                
            logging.info(f"完成处理文件: {self.file_path} 使用引擎: {self.asr_engine}")
            save_path = self.file_path.rsplit(".", 1)[0] + "." + save_ext
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(result_text)

            #生成视频
            if self.ui_self.video_checkbox.isChecked():
                logging.info(f"开始视频合成: {self.file_path},img_file:{self.ui_self.img_file}")
                temp_video = self.file_path.rsplit(".", 1)[0] + ".mp4"
                if self.ui_self.img_file is None:
                    self.ui_self.img_file=""
                if not audio2video(self.ui_self.img_file,self.audio_path,self.ui_self.video_par_s_combo.currentText(),self.ui_self.video_par_r_spin.value(),float(self.ui_self.video_par_p_spin.value()/100), temp_video):
                    raise Exception("视频合成视频失败，确保安装ffmpeg")
                if temp_audio != "default":
                    os.unlink(temp_audio)
                logging.info(f"完成视频合成: {self.file_path}")
            else:
                if temp_audio != "default":
                    os.unlink(temp_audio)

            self.signals.finished.emit(self.file_path, result_text)
        except Exception as e:
            logging.error(f"处理文件 {self.file_path} 时出错: {str(e)}")
            self.signals.errno.emit(self.file_path, f"处理时出错: {str(e)}")

class MyLineEdit(LineEdit):
    def __init__(self,thatself):
        super().__init__()
        self.setAcceptDrops(True)  # 设置可以接受拖动
        self.thatself=thatself
        self.filetype = 'media'
    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if self.filetype=='image':
            supported_formats = ('.png', '.jpg','jpeg')
            files = [u.toLocalFile() for u in event.mimeData().urls()]
            for file in files:
                if os.path.isdir(file):
                    return
                elif file.lower().endswith(supported_formats):
                    self.thatself.img_file = file
                    self.setPlaceholderText(str(file))
                    self.setToolTip(str(file))
        else:
            supported_formats = ('.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma',  # 音频格式
                                 '.mp4', '.avi', '.mov', '.ts', '.mkv', '.wmv', '.flv', '.webm', '.rmvb')  # 视频格式
            files = [u.toLocalFile() for u in event.mimeData().urls()]
            for file in files:
                if os.path.isdir(file):
                    for root, dirs, files_in_dir in os.walk(file):
                        for f in files_in_dir:
                            if f.lower().endswith(supported_formats):
                                self.thatself.add_file_to_table(os.path.join(root, f))
                elif file.lower().endswith(supported_formats):
                    self.thatself.add_file_to_table(file)
            self.thatself.update_start_button_state()

class ASRWidget(QWidget):
    """ASR处理界面"""

    def __init__(self):
        super().__init__()
        self.img_input = None
        self.img_file = None
        self.video_par_s_combo = None
        self.video_par_r_spin = None
        self.video_par_v_combo = None
        self.video_par_frame = None
        self.video_checkbox = None
        self.combo_box = None
        self.format_combo = None
        self.init_ui()
        self.max_threads = os.cpu_count()-1  # 设置最大线程数
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(self.max_threads)
        self.processing_queue = []
        self.workers = {}  # 维护文件路径到worker的映射

    def set_img_file(self,file):
        self.img_file=file

    def init_ui(self):
        layout = QVBoxLayout(self)

        media2srt=QHBoxLayout()
        #并发任务数
        tasks_label = BodyLabel("同时最大任务数:", self)
        tasks_label.setFixedWidth(120)
        self.tasks_spin = SpinBox(self)
        # self.video_par_r_spin.setFixedHeight(35)
        self.tasks_spin.setRange(1, os.cpu_count()-1)
        self.tasks_spin.setValue(math.ceil(os.cpu_count()/2))
        self.tasks_spin.setSingleStep(1)
        media2srt.addWidget(tasks_label)
        media2srt.addWidget(self.tasks_spin)
        # ASR引擎选择区域
        # engine_layout = QHBoxLayout()
        engine_label = BodyLabel("音频转字幕接口:", self)
        engine_label.setFixedWidth(120)
        self.combo_box = ComboBox(self)
        self.combo_box.addItems(['B 接口', 'J 接口', 'K 接口', 'Whisper'])
        # engine_layout.addWidget(engine_label)
        # engine_layout.addWidget(self.combo_box)
        # layout.addLayout(engine_layout)
        media2srt.addWidget(engine_label)
        media2srt.addWidget(self.combo_box)
        # 导出格式选择区域 
        # format_layout = QHBoxLayout()
        format_label = BodyLabel("输出字幕格式:", self)
        format_label.setFixedWidth(120)
        self.format_combo = ComboBox(self)
        self.format_combo.addItems(['SRT', 'TXT', 'ASS'])
        # format_layout.addWidget(format_label)
        # format_layout.addWidget(self.format_combo)
        # layout.addLayout(format_layout)
        media2srt.addWidget(format_label)
        media2srt.addWidget(self.format_combo)
        layout.addLayout(media2srt)
        # 是否生成视频选项
        video_check_layout = QHBoxLayout()
        video_check_label = BodyLabel("图片生成视频:", self)
        video_check_label.setFixedWidth(90)
        self.video_checkbox = QCheckBox()
        self.video_checkbox.setChecked(False)
        self.video_checkbox.stateChanged.connect(self.video_checkbox_state_changed)
        video_check_layout.addWidget(video_check_label)
        video_check_layout.addWidget(self.video_checkbox)
        layout.addLayout(video_check_layout)

        #音频生成视频的参数
        self.video_par_frame = QFrame()
        video_par_layout=QVBoxLayout()
        video_par_v_layout = QHBoxLayout()
        # video_par_v_label = BodyLabel("视频流:", self)
        # video_par_v_label.setFixedWidth(70)
        # self.video_par_v_combo = ComboBox(self)
        # self.video_par_v_combo.addItems(['默认空图片', '添加图片'])
        # video_par_v_layout.addWidget(video_par_v_label)
        # video_par_v_layout.addWidget(self.video_par_v_combo)

        video_par_r_label = BodyLabel("帧率:", self)
        video_par_r_label.setFixedWidth(70)
        self.video_par_r_spin = SpinBox(self)
        # self.video_par_r_spin.setFixedHeight(35)
        self.video_par_r_spin.setRange(1,60)
        self.video_par_r_spin.setValue(30)
        self.video_par_r_spin.setSingleStep(10)
        video_par_v_layout.addWidget(video_par_r_label)
        video_par_v_layout.addWidget(self.video_par_r_spin)

        video_par_s_label = BodyLabel("分辨率:", self)
        video_par_s_label.setFixedWidth(70)
        self.video_par_s_combo = ComboBox(self)
        self.video_par_s_combo.addItems(['640x360','852x480', '1280x720','1920x1080','3840x2160'])
        self.video_par_s_combo.setCurrentIndex(2)
        video_par_v_layout.addWidget(video_par_s_label)
        video_par_v_layout.addWidget(self.video_par_s_combo)

        video_par_layout.addLayout(video_par_v_layout)
        # 图片文件选择区域
        img_layout = QHBoxLayout()
        self.img_input = MyLineEdit(self)
        self.img_input.setPlaceholderText("拖拽图片文件到这里.为空时，会优先使用同名图片文件。")
        self.img_input.setToolTip("拖拽图片文件到这里。为空时，会优先使用同名图片文件。")
        self.img_input.setReadOnly(True)
        self.img_input.filetype='image'
        self.img_button = PushButton("选择图片文件", self)
        self.img_button.clicked.connect(self.select_img_file)
        img_layout.addWidget(self.img_input)
        img_layout.addWidget(self.img_button)

        video_par_p_label = BodyLabel("字幕预留高度(%):", self)
        video_par_p_label.setToolTip('使用图片时生效，视频上下均会空出该区域')
        video_par_p_label.setFixedWidth(120)
        self.video_par_p_spin = SpinBox(self)
        # self.video_par_r_spin.setFixedHeight(35)
        self.video_par_p_spin.setRange(0, 30)
        self.video_par_p_spin.setValue(12)
        self.video_par_p_spin.setSingleStep(5)
        img_layout.addWidget(video_par_p_label)
        img_layout.addWidget(self.video_par_p_spin)

        video_par_layout.addLayout(img_layout)

        self.video_par_frame.setLayout(video_par_layout)
        layout.addWidget(self.video_par_frame)
        self.video_par_frame.hide()

        # 文件选择区域
        file_layout = QHBoxLayout()
        self.file_input = MyLineEdit(self)
        self.file_input.setPlaceholderText("拖拽视频或音频文件或文件夹到这里")
        self.file_input.setReadOnly(True)
        self.file_button = PushButton("选择文件", self)
        self.file_button.clicked.connect(self.select_file)
        file_layout.addWidget(self.file_input)
        file_layout.addWidget(self.file_button)
        layout.addLayout(file_layout)


        # 文件列表表格
        self.table = TableWidget(self)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(['文件名', '状态'])
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.table)

        # 设置表格列的拉伸模式
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 100)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 按钮
        button_layout = QHBoxLayout()
        self.clear_button = PushButton("清空已完成", self)
        self.clear_button.clicked.connect(self.deletefiles)
        self.clear_button.setEnabled(False)  # 初始禁用
        button_layout.addWidget(self.clear_button)

        self.process_button = PushButton("开始处理", self)
        self.process_button.clicked.connect(self.process_files)
        self.process_button.setEnabled(False)  # 初始禁用
        # layout.addWidget(self.process_button)
        button_layout.addWidget(self.process_button)
        layout.addLayout(button_layout)
        # self.setAcceptDrops(True)

    def video_checkbox_state_changed(self, state):
        if state == 2:
            self.video_par_frame.show()
        else:
            self.video_par_frame.hide()
    def select_img_file(self):
        """选择图片文件对话框"""
        file, _ = QFileDialog.getOpenFileName(self, "选择图片文件", "",
                                                "Image (*.png *.jpg *.jpeg)")
        self.img_file=file
    def select_file(self):
        """选择文件对话框"""
        files, _ = QFileDialog.getOpenFileNames(self, "选择音频或视频文件", "",
                                                "Media Files (*.mp3 *.wav *.ogg *.mp4 *.avi *.mov *.ts)")
        for file in files:
            self.add_file_to_table(file)
        self.update_start_button_state()

    def add_file_to_table(self, file_path):
        """将文件添加到表格中"""
        if self.find_row_by_file_path(file_path) != -1:
            InfoBar.warning(
                title='文件已存在',
                content=f"文件 {os.path.basename(file_path)} 已经添加到列表中。",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self
            )
            return

        row_count = self.table.rowCount()
        self.table.insertRow(row_count)
        item_filename = self.create_non_editable_item(os.path.basename(file_path))
        item_status = self.create_non_editable_item("未处理")
        item_status.setForeground(QColor("gray"))
        self.table.setItem(row_count, 0, item_filename)
        self.table.setItem(row_count, 1, item_status)
        item_filename.setData(Qt.UserRole, file_path)

    def create_non_editable_item(self, text):
        """创建不可编辑的表格项"""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def show_context_menu(self, pos):
        """显示右键菜单"""
        current_row = self.table.rowAt(pos.y())
        if current_row < 0:
            return

        self.table.selectRow(current_row)

        menu = RoundMenu(self)
        reprocess_action = Action(FluentIcon.SYNC, "重新处理")
        delete_action = Action(FluentIcon.DELETE, "删除任务")
        open_dir_action = Action(FluentIcon.FOLDER, "打开文件目录")
        menu.addActions([reprocess_action, delete_action, open_dir_action])

        delete_action.triggered.connect(self.delete_selected_row)
        open_dir_action.triggered.connect(self.open_file_directory)
        reprocess_action.triggered.connect(self.reprocess_selected_file)

        menu.exec(QCursor.pos())

    def deletefiles(self):
        del_nums=0
        for row in range(self.table.rowCount()):
            row=row-del_nums
            if row >=self.table.rowCount():
                return
            if self.table.item(row, 1).text() == "已处理":
                file_path = self.table.item(row, 0).data(Qt.UserRole)
                if file_path in self.workers:
                    worker = self.workers[file_path]
                    worker.signals.finished.disconnect(self.update_table)
                    worker.signals.errno.disconnect(self.handle_error)
                    # QThreadPool 不支持直接终止线程，通常需要设计任务可中断
                    # 这里仅移除引用
                    self.workers.pop(file_path, None)
                self.table.removeRow(row)
                del_nums=del_nums+1
        self.update_start_button_state()
    def delete_selected_row(self):
        """删除选中的行"""
        current_row = self.table.currentRow()
        if current_row >= 0:
            file_path = self.table.item(current_row, 0).data(Qt.UserRole)
            if file_path in self.workers:
                worker = self.workers[file_path]
                worker.signals.finished.disconnect(self.update_table)
                worker.signals.errno.disconnect(self.handle_error)
                # QThreadPool 不支持直接终止线程，通常需要设计任务可中断
                # 这里仅移除引用
                self.workers.pop(file_path, None)
            self.table.removeRow(current_row)
            self.update_start_button_state()

    def open_file_directory(self):
        """打开文件所在目录"""
        current_row = self.table.currentRow()
        if current_row >= 0:
            current_item = self.table.item(current_row, 0)
            if current_item:
                file_path = current_item.data(Qt.UserRole)
                directory = os.path.dirname(file_path)
                try:
                    if platform.system() == "Windows":
                        os.startfile(directory)
                    elif platform.system() == "Darwin":
                        subprocess.Popen(["open", directory])
                    else:
                        subprocess.Popen(["xdg-open", directory])
                except Exception as e:
                    InfoBar.error(
                        title='无法打开目录',
                        content=str(e),
                        orient=Qt.Horizontal,
                        isClosable=True,
                        position=InfoBarPosition.TOP,
                        duration=3000,
                        parent=self
                    )

    def reprocess_selected_file(self):
        """重新处理选中的文件"""
        current_row = self.table.currentRow()
        if current_row >= 0:
            file_path = self.table.item(current_row, 0).data(Qt.UserRole)
            status = self.table.item(current_row, 1).text()
            if status == "处理中":
                InfoBar.warning(
                    title='当前文件正在处理中',
                    content="请等待当前文件处理完成后再重新处理。",
                    orient=Qt.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self
                )
                return
            self.add_to_queue(file_path)

    def add_to_queue(self, file_path):
        """将文件添加到处理队列并更新状态"""
        self.processing_queue.append(file_path)
        self.process_next_in_queue()

    def process_files(self):
        """处理所有未处理的文件"""
        self.thread_pool.setMaxThreadCount(int(self.tasks_spin.value()))
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1).text() == "未处理":
                file_path = self.table.item(row, 0).data(Qt.UserRole)
                self.processing_queue.append(file_path)
        self.process_next_in_queue()

    def process_next_in_queue(self):
        """处理队列中的下一个文件"""
        while self.thread_pool.activeThreadCount() < self.max_threads and self.processing_queue:
            file_path = self.processing_queue.pop(0)
            if file_path not in self.workers:
                self.process_file(file_path)

    def process_file(self, file_path):
        """处理单个文件"""
        selected_engine = self.combo_box.currentText()
        selected_format = self.format_combo.currentText()
        worker = ASRWorker(file_path, selected_engine, selected_format,self)
        worker.signals.finished.connect(self.update_table)
        worker.signals.errno.connect(self.handle_error)
        self.thread_pool.start(worker)
        self.workers[file_path] = worker

        row = self.find_row_by_file_path(file_path)
        if row != -1:
            status_item = self.create_non_editable_item("处理中")
            status_item.setForeground(QColor("orange"))
            self.table.setItem(row, 1, status_item)
            self.update_start_button_state()

    def update_table(self, file_path, result):
        """更新表格中文件的处理状态"""
        row = self.find_row_by_file_path(file_path)
        if row != -1:
            item_status = self.create_non_editable_item("已处理")
            item_status.setForeground(QColor("green"))
            self.table.setItem(row, 1, item_status)

            InfoBar.success(
                title='处理完成',
                content=f"文件 {self.table.item(row, 0).text()} 已处理完成",
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=1500,
                parent=self
            )

        self.workers.pop(file_path, None)
        self.process_next_in_queue()
        self.update_start_button_state()

    def handle_error(self, file_path, error_message):
        """处理错误信息"""
        row = self.find_row_by_file_path(file_path)
        if row != -1:
            item_status = self.create_non_editable_item("错误")
            item_status.setForeground(QColor("red"))
            self.table.setItem(row, 1, item_status)

            InfoBar.error(
                title='处理出错',
                content=error_message,
                orient=Qt.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self
            )

        self.workers.pop(file_path, None)
        self.process_next_in_queue()
        self.update_start_button_state()

    def find_row_by_file_path(self, file_path):
        """根据文件路径查找表格中的行号"""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.data(Qt.UserRole) == file_path:
                return row
        return -1

    def update_start_button_state(self):
        """根据文件列表更新开始处理按钮的状态"""
        has_unprocessed = any(
            self.table.item(row, 1).text() == "未处理"
            for row in range(self.table.rowCount())
        )
        self.process_button.setEnabled(has_unprocessed)
        has_processed = any(
            self.table.item(row, 1).text() == "已处理"
            for row in range(self.table.rowCount())
        )
        self.clear_button.setEnabled(has_processed)

    def update_clear_button_state(self):
        """根据文件列表更新开始处理按钮的状态"""
        has_unprocessed = any(
            self.table.item(row, 1).text() == "已处理"
            for row in range(self.table.rowCount())
        )
        self.clear_button.setEnabled(has_unprocessed)
    # def dragEnterEvent(self, event):
    #     """拖拽进入事件"""
    #     if event.mimeData().hasUrls():
    #         event.accept()
    #     else:
    #         event.ignore()
    #
    # def dropEvent(self, event):
    #     """拖拽释放事件"""
    #     supported_formats = ('.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma',  # 音频格式
    #                        '.mp4', '.avi', '.mov', '.ts', '.mkv', '.wmv', '.flv', '.webm', '.rmvb')  # 视频格式
    #     files = [u.toLocalFile() for u in event.mimeData().urls()]
    #     for file in files:
    #         if os.path.isdir(file):
    #             for root, dirs, files_in_dir in os.walk(file):
    #                 for f in files_in_dir:
    #                     if f.lower().endswith(supported_formats):
    #                         self.add_file_to_table(os.path.join(root, f))
    #         elif file.lower().endswith(supported_formats):
    #             self.add_file_to_table(file)
    #     self.update_start_button_state()


class InfoWidget(QWidget):
    """个人信息界面"""

    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        # GitHub URL 和仓库描述
        GITHUB_URL = "https://github.com/NuclearHe/AsrTools"
        REPO_DESCRIPTION = """
    🚀 无需复杂配置：无需 GPU 和繁琐的本地配置，小白也能轻松使用。
    🖥️ 高颜值界面：基于 PyQt5 和 qfluentwidgets，界面美观且用户友好。
    ⚡ 效率超人：多线程并发 + 批量处理，文字转换快如闪电。
    📄 多格式支持：支持生成 .srt 和 .txt 字幕文件，满足不同需求。
    原仓库：https://github.com/WEIFENG2333/AsrTools
    新增功能：
     1.支持音频+指定图片生成视频文件。
     2.多线程调整为（核心数-1）
        """
        
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignTop)
        # main_layout.setSpacing(50)

        # 标题
        title_label = BodyLabel("  ASRTools", self)
        title_label.setFont(QFont("Segoe UI", 30, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # 仓库描述区域
        desc_label = BodyLabel(REPO_DESCRIPTION, self)
        desc_label.setFont(QFont("Segoe UI", 12))
        main_layout.addWidget(desc_label)

        github_button = PushButton("GitHub 仓库", self)
        github_button.setIcon(FluentIcon.GITHUB)
        github_button.setIconSize(QSize(20, 20))
        github_button.setMinimumHeight(42)
        github_button.clicked.connect(lambda _: webbrowser.open(GITHUB_URL))
        main_layout.addWidget(github_button)


class MainWindow(FluentWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ASR Processing Tool')

        # ASR 处理界面
        self.asr_widget = ASRWidget()
        self.asr_widget.setObjectName("main")
        self.addSubInterface(self.asr_widget, FluentIcon.ALBUM, 'ASR Processing')

        # 个人信息界面
        self.info_widget = InfoWidget()
        self.info_widget.setObjectName("info")  # 设置对象名称
        self.addSubInterface(self.info_widget, FluentIcon.GITHUB, 'About')

        self.navigationInterface.setExpandWidth(200)
        self.resize(800, 600)

        # self.update_checker = UpdateCheckerThread(self)
        # self.update_checker.msg.connect(self.show_msg)
        # self.update_checker.start()

    def show_msg(self, title, content, update_download_url):
        w = MessageBox(title, content, self)
        if w.exec() and update_download_url:
            webbrowser.open(update_download_url)
        if title == "更新":
            sys.exit(0)

def video2audio(input_file: str, output: str = "") -> bool:
    """使用ffmpeg将视频转换为音频"""
    # 创建output目录
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = str(output)

    cmd = [
        'ffmpeg',
        '-i', input_file,
        '-ac', '1',
        '-f', 'mp3',
        '-af', 'aresample=async=1',
        '-y',
        output
    ]
    result = subprocess.run(cmd, capture_output=True, check=True, encoding='utf-8', errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)

    if result.returncode == 0 and Path(output).is_file():
        return True
    else:
        return False

def audio2video(video_file:str,audio_file: str,scale:str,rate:int,padding, output: str = "") -> bool:
    """使用ffmpeg将视频转换为音频"""
    # 创建output目录
    if video_file is None:
        video_file=""
    output=output.rsplit(".", 1)[0] + '_rate' + str(rate) + '_scale' + scale + ".mp4"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output = str(output)
    resize_file = video_file
    tmp_file=False
    if video_file =="":
        supported_formats = ('.png', '.jpg', '.jpeg')
        files = [audio_file.rsplit(".", 1)[0]+u for u in supported_formats if os.path.isfile(audio_file.rsplit(".", 1)[0]+u)]
        if len(files)!=0:
            resize_file = img_resize(files[0], int(scale.split("x")[0]), int(scale.split("x")[1]), 15, padding)
            tmp_file = True
        else:
            resize_file = os.path.join(os.path.dirname(__file__), 'static/100.png')
    else:
        # resize_file=video_file.rsplit(".", 1)[0] + '_resize'+ ".png"
        resize_file=img_resize(video_file, int(scale.split("x")[0]), int(scale.split("x")[1]), 15,padding)
        tmp_file=True

    # tmp_image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    # video_file = output.rsplit(".", 1)[0] + ".png"
    # tmp_image.save(video_file)

    cmd = [
        'ffmpeg',
        '-loop','1',
        '-i', resize_file,
        '-i', audio_file,
        '-r', str(rate),#帧数
        '-s', scale,#1280x720  '640x360'
        '-pix_fmt', 'yuv420p',
        '-c:v', 'libx264',
        '-c:a', 'copy',
        '-shortest',
        '-y',
        output
    ]
    logging.info(f'cmd:{cmd}')
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, encoding='utf-8', errors='replace')
        if tmp_file:
            os.unlink(resize_file)
    except Exception as err:
        logging.error(str(err))
    if result.returncode == 0 and Path(output).is_file():
        return True
    else:
        return False

def start():
    # enable dpi scale
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    # setTheme(Theme.DARK)  # 如果需要深色主题，取消注释此行
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    start()
