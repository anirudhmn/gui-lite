import sys
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import pyqtgraph as pg
from queue import Queue, Empty
import flexemgComm
import time
import numpy as np
import scipy.io as sio
import datetime
import os
from GestureDefs import *

# for writing stdout to text box
class StdoutHandler(QObject):
    written = pyqtSignal(str)

    def __init__(self, parent = None):
        QObject.__init__(self, parent)

    def write(self, data):
        self.written.emit(str(data))

class cp2130Thread(QThread):
    def __init__(self):
        QThread.__init__(self)
        self._running = False

    def __del__(self):
        self.wait()

    def stop(self):
        self._running = False

    def run(self):
        if not self._running:
            self._running = True
            flexemgComm.cp2130_libusb_flush_radio_fifo(cp2130Handle)
            sampleQueue.queue.clear()
            time.sleep(0.1)
            flexemgComm.startStream(cp2130Handle)
            time.sleep(0.1)

        while self._running:
            data = flexemgComm.cp2130_libusb_read(cp2130Handle)
            if data:
                if data[1] == 198:
                    sampleQueue.put(data)

        flexemgComm.stopStream(cp2130Handle)
        time.sleep(0.1)

    def setWideIn(self, mode):
        success = False
        if not self._running:
            val, readSuccess = flexemgComm.readReg(cp2130Handle,0,0x0C)
            if not readSuccess:
                print('Failed to read wide input register!')
            else:
                value = val
                if mode:
                    # trying to enable wide input
                    if not (value % 2):
                        # wide input is disabled
                        value = value + 1
                        if (flexemgComm.writeReg(cp2130Handle,0,0x0C,value)):
                            print('Wide input mode enabled!')
                            success = True
                        else:
                            print('Unable to write wide input register!')
                    else:
                        print('Wide input already enabled!')
                else:
                    # trying to disable wide input
                    if (value % 2):
                        # wide input is enabled
                        value = value - 1
                        if (flexemgComm.writeReg(cp2130Handle,0,0x0C,value)):
                            print('Wide input mode disabled!')
                            success = True
                        else:
                            print('Unable to write wide input register!')
                    else:
                        print('Wide input already disabled!')
        else:
            print('Cannot set wide input while streaming!')
        return success

class processThread(QThread):

    plotDataReady = pyqtSignal(list, int)
    messageTick = pyqtSignal()

    def __init__(self):
        QThread.__init__(self)
        self._running = False
        self.samples = 0
        self.matOut = {}

    def __del__(self):
        self.wait()

    def stop(self,saveDataChecked):
        self._running = False
        self.saveDataChecked = saveDataChecked

    def run(self):
        if not self._running:
            self._running = True
            self.samples = 0
            plotData = []
            self.saveData = []
            self.crcFlag = []
            self.crcSamples = 0
            print('Beginning stream')
            self.numMins = 0

        while self._running:
            try:
                data = sampleQueue.get(block=False)
                self.samples = self.samples + 1
                if data[0]==0x00: # no CRC
                    self.values = [(data[2*(i+1) + 1] << 8 | data[2*(i+1)]) & 0xFFFF for i in range(0,67)]
                    plotData.append(self.values)
                else: # has CRC
                    self.crcSamples += 1
                    plotData.append(self.values)
                    self.values = [(data[2*(i+1) + 1] << 8 | data[2*(i+1)]) & 0xFFFF for i in range(0,67)]
                self.saveData.append(self.values)
                self.crcFlag.append(data[0])
                if self.samples % 50 == 0:
                    self.plotDataReady.emit(plotData, self.samples)
                    plotData = []
                if self.samples % 60000 == 0:
                    self.numMins += 1
                    print('    Streaming for {} mins'.format(self.numMins))
                if self.samples % 1000 == 0:
                    self.messageTick.emit()
            except Empty:
                time.sleep(0.0001)

        if self.saveData:
            print("Received total of {} samples".format(self.samples))
            print("    with {} CRCs (error rate = {})".format(self.crcSamples, self.crcSamples/self.samples))
            if self.saveDataChecked:
                saveDir = '/Users/ally.menon/Documents/Grad School/Research/HD Feedback/GUI/HDCRORB_forceemg_gui/gui-lite/data/'
                if not os.path.exists(saveDir):
                    os.makedirs(saveDir)
                matData = np.asarray(self.saveData)
                matCrc = np.asarray(self.crcFlag)
                self.matOut['raw'] = matData
                self.matOut['crc'] = matCrc
                filename = 'S'
                if 'subject' in self.matOut.keys():
                    filename += str(self.matOut['subject']).zfill(3)
                filename += '_'
                filename += 'E'
                if 'experiment' in self.matOut.keys():
                    filename += str(self.matOut['experiment']).zfill(3)
                filename += '_'
                filename += 'R'
                if 'reps' in self.matOut.keys():
                    filename += str(self.matOut['reps']).zfill(3)
                filename += '_'
                filename += 'G'
                if 'gestureSecs' in self.matOut.keys():
                    filename += str(self.matOut['gestureSecs']).zfill(3)
                filename += '_'
                filename += datetime.datetime.now().strftime("%Y%m%d%H%M%S.mat")


                sio.savemat(saveDir+filename, self.matOut)
                print("Data saved at " + "\n    " + saveDir + filename)
                self.matOut = {}

    def setMeta(self,matOut):
        self.matOut = matOut

    def appendEmptyRow(self, x):
        self.saveData.append([x for i in range(0,67)])
        self.crcFlag.append(1)

# subclass QMainWindow to customize
class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        # create main window
        self.setWindowTitle("FlexEMG GUI Lite")
        # create layout
        self.layout = QGridLayout()

        # plotting area
        self.plotArea = pg.GraphicsLayoutWidget()
        self.numPlots = 5
        self.xRange = 2000
        self.plots = []
        self.plotLineRefs = []
        self.plotScrollData = []
        self.plotPlaceData = []
        self.plotXPlace = 0
        self.plotTime = list(range(-self.xRange,0))

        # channel selection boxes
        self.plotCh = []
        for i in range(0, self.numPlots):
            self.plotCh.append(QSpinBox())
            self.plotCh[i].setMinimum(0)
            self.plotCh[i].setMaximum(66)
            self.plotCh[i].setSingleStep(1)
        self.plotCh[0].setValue(0)
        self.plotCh[1].setValue(1)
        self.plotCh[2].setValue(2)
        self.plotCh[3].setValue(3)
        self.plotCh[4].setValue(4)
        # scroll style selection
        self.scrollStyle = QComboBox()
        self.scrollStyle.addItem('Plot continous scroll')
        self.scrollStyle.addItem('Plot in place')

        for i in range(0, self.numPlots):
            viewBox = pg.ViewBox(enableMouse=False)
            self.plots.append(self.plotArea.addPlot(row=i,col=0,viewBox=viewBox))
            self.plotScrollData.append([0]*self.xRange)
            self.plotPlaceData.append([0]*self.xRange)
            if self.scrollStyle.currentIndex() == 0:
                self.plotLineRefs.append(self.plots[i].plot(x=self.plotTime,y=self.plotScrollData[i]))
            if self.scrollStyle.currentIndex() == 1:
                self.plotLineRefs.append(self.plots[i].plot(y=self.plotPlaceData[i]))

        # Effort level
        self.effort = QProgressBar()
        self.effort.setMinimum(0)
        self.effort.setMaximum(1000)
        self.effort.setValue(500)

        self.effortControlled = QProgressBar()
        self.effortControlled.setMinimum(0)
        self.effortControlled.setMaximum(1000)
        self.effortControlled.setValue(500)

        # basic stream button
        self.streamButton = QPushButton('Stream from Ch:')
        self.streamButton.setCheckable(True)
        self.streamButton.clicked.connect(self.stream)

        # save checkbox
        self.saveDataCheck = QCheckBox('Save stream to file')
        self.saveDataCheck.setCheckable(True)
        self.saveDataCheck.setChecked(False)

        # experiment checkbox
        self.expCheck = QCheckBox('Run experiment')
        self.expCheck.setCheckable(True)
        self.expCheck.setChecked(False)

        # wide input checkbox
        self.wideCheck = QCheckBox('Wide input')
        self.wideCheck.setCheckable(True)
        self.wideCheck.setChecked(False)
        self.wideCheck.clicked.connect(self.wideSet)

        # stdout output
        self.stdoutText  = QTextEdit()
        self.stdoutText.moveCursor(QTextCursor.Start)
        self.stdoutText.ensureCursorVisible()
        self.stdoutText.setLineWrapMode(QTextEdit.NoWrap)

        # gesture instruction message
        self.relaxSeconds = 3
        self.message = QLabel("Calibrating\nPut the bottle down and rest")
        font = QFont()
        font.setFamily("Helvetica")
        font.setPointSize(30)
        self.message.setFont(font)
        self.message.setAlignment(Qt.AlignLeft|Qt.AlignVCenter)

        # position instruction image
        self.posImage = QLabel()

        # gesture information
        self.subjectLabel = QLabel('Subject:')
        self.expLabel = QLabel('Experiment:')
        self.repsLabel = QLabel('# Repetitions:')
        self.gestLenLabel = QLabel('Gesture length (s):')
        self.bufferLenLabel = QLabel('Buffer length (s):')

        # gesture information selection
        self.subjectSelect = QSpinBox()
        self.subjectSelect.setValue(1)
        self.subjectSelect.setMinimum(1)
        self.subjectSelect.setMaximum(999)
        self.subjectSelect.setSingleStep(1)

        self.expSelect = QSpinBox()
        self.expSelect.setValue(0)
        self.expSelect.setMinimum(0)
        self.expSelect.setMaximum(999)
        self.expSelect.setSingleStep(1)

        self.numReps = QSpinBox()
        self.numReps.setValue(10)
        self.numReps.setMinimum(1)
        self.numReps.setMaximum(50)
        self.numReps.setSingleStep(1)

        self.gestureLen = QSpinBox()
        self.gestureLen.setValue(10)
        self.gestureLen.setMinimum(1)
        self.gestureLen.setMaximum(50)
        self.gestureLen.setSingleStep(1)

        self.bufferLen = QSpinBox()
        self.bufferLen.setValue(5)
        self.bufferLen.setMinimum(1)
        self.bufferLen.setMaximum(50)
        self.bufferLen.setSingleStep(1)

        self.descLabel = QLabel('Description:')
        self.desc = QLineEdit()

        # add widgets to layout
        self.layout.addWidget(self.plotArea,0,0,10,6)
        self.layout.addWidget(self.effort,10,0,2,6)
        self.layout.addWidget(self.effortControlled,12,0,2,6)
        self.layout.addWidget(self.streamButton,14,0,1,1)
        self.layout.addWidget(self.plotCh[0],14,1,1,1)
        self.layout.addWidget(self.plotCh[1],14,2,1,1)
        self.layout.addWidget(self.plotCh[2],14,3,1,1)
        self.layout.addWidget(self.plotCh[3],14,4,1,1)
        self.layout.addWidget(self.plotCh[4],14,5,1,1)
        self.layout.addWidget(self.scrollStyle,15,0,1,2)
        self.layout.addWidget(self.saveDataCheck,15,2,1,1)
        self.layout.addWidget(self.expCheck,15,3,1,1)
        self.layout.addWidget(self.wideCheck,15,4,1,1)

        self.layout.addWidget(self.message,0,6,2,8)
        self.layout.addWidget(self.posImage,2,6,8,4)
        self.layout.addWidget(self.subjectLabel,11,6,1,2)
        self.layout.addWidget(self.expLabel,11,8,1,2)
        self.layout.addWidget(self.subjectSelect,12,6,1,2)
        self.layout.addWidget(self.expSelect,12,8,1,2)
        self.layout.addWidget(self.repsLabel,13,6,1,2)
        self.layout.addWidget(self.gestLenLabel,13,8,1,2)
        self.layout.addWidget(self.bufferLenLabel,13,10,1,2)
        self.layout.addWidget(self.numReps,14,6,1,2)
        self.layout.addWidget(self.gestureLen,14,8,1,2)
        self.layout.addWidget(self.bufferLen,14,10,1,2)
        self.layout.addWidget(self.descLabel,15,6,1,1)
        self.layout.addWidget(self.desc,15,7,1,5)

        self.layout.addWidget(self.stdoutText,16,0,1,17)

        self.widget = QWidget()
        self.widget.setLayout(self.layout)

        # put widget contents into main window
        self.setCentralWidget(self.widget)

        self.cp2130Thread = cp2130Thread()
        self.processThread = processThread()
        self.processThread.plotDataReady.connect(self.plotDataReady)
        self.processThread.messageTick.connect(self.messageTick)

        # connect stdout to text box
        self.stdHandler = StdoutHandler()
        self.stdHandler.written.connect(self.onUpdateText)
        sys.stdout = self.stdHandler

        self.imageDir = 'Gestures/'
        self.posImage.setPixmap(QPixmap(self.imageDir + "rest.png").scaledToWidth(int(self.posImage.geometry().width()/1.78)))

    def __del__(self):
        sys.stdout = sys.__stdout__

    def onUpdateText(self, text):
        cursor = self.stdoutText.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.stdoutText.setTextCursor(cursor)
        self.stdoutText.ensureCursorVisible()

    def stream(self):
        # starting and stopping stream with single button
        if self.streamButton.isChecked():
            self.expCheck.setEnabled(False)
            self.subjectSelect.setEnabled(False)
            self.expSelect.setEnabled(False)
            self.wideCheck.setEnabled(False)
            self.numReps.setEnabled(False)
            self.gestureLen.setEnabled(False)
            self.desc.setEnabled(False)
            self.expCheck.setEnabled(False)

            # set up experiment timing here
            if self.expCheck.isChecked():
                self.messageList = []
                self.posImageList = []

                self.forceCalibration = []
                self.forceMinimum = 0
                self.forceMaximum = 0
                self.initialSample = 0
                self.firstInitialSample = True

                # get strings for reuse
                subject = self.subjectSelect.value()
                experiment = self.expSelect.value()
                reps = self.numReps.value()
                relaxSecs = self.relaxSeconds
                gestureSecs = self.gestureLen.value()
                bufferSecs = self.bufferLen.value()
                calibrationSecs = 5

                matOut = {}
                matOut['subject'] = subject
                matOut['experiment'] = experiment
                matOut['reps'] = reps
                matOut['bufferSecs'] = bufferSecs
                matOut['gestureSecs'] = gestureSecs

                # Calibrate effor bar
                self.messageList.append('Hello')
                for x in range(bufferSecs,0,-1):
                    self.messageList.append('Calibrating\nPut the bottle down and rest in ' + str(x))
                    self.posImageList.append('rest')
                for x in range(calibrationSecs,0,-1):
                    self.messageList.append('Calibrating\nHold rest position for ' + str(x))
                    self.posImageList.append('rest')
                for x in range(bufferSecs,0,-1):
                    self.messageList.append('Calibrating\nStart squeezing as hard as you can in ' + str(x))
                    self.posImageList.append('squeeze')
                for x in range(calibrationSecs,0,-1):
                    self.messageList.append('Calibrating\nHold squeezing as hard as you can for ' + str(x))
                    self.posImageList.append('squeeze')

                for x in range(2,0,-1):
                    self.messageList.append('Relax\nRest for ' + str(x))
                    self.posImageList.append('relax')

                for x in range(1,reps+1):
                    for s in range(bufferSecs,0,-1):
                        self.messageList.append('Get Ready\nTry to slowly increase the amount of\nforce and match the lower bar in ' + str(s))
                        self.posImageList.append('squeeze')
                    for s in range(gestureSecs,0,-1):
                        self.messageList.append('Sqeeze\nApply force to the bottle and match the\nlower bar for ' + str(s) + ' more seconds')
                        self.posImageList.append('squeeze')
                    for s in range(relaxSecs,0,-1):
                        self.messageList.append('Relax for ' + str(s))
                        self.posImageList.append('relax')

                # all done
                for s in range(relaxSecs,0,-1):
                    self.messageList.append('Relax\nDone in ' + str(s) + ' seconds')
                    self.posImageList.append('rest')
                self.messageList.append('Done!\n')
                self.posImageList.append('rest')

                self.messageIdx = 0
                self.numMessages = len(self.messageList)

                self.processThread.setMeta(matOut)
            else:
                self.processThread.setMeta({})

            self.processThread.start()
            self.cp2130Thread.start()
        else:
            self.cp2130Thread.stop()
            self.processThread.stop(self.saveDataCheck.isChecked())

            self.expCheck.setEnabled(True)
            self.subjectSelect.setEnabled(True)
            self.expSelect.setEnabled(True)
            self.wideCheck.setEnabled(True)
            self.numReps.setEnabled(True)
            self.gestureLen.setEnabled(True)
            self.desc.setEnabled(True)
            self.expCheck.setEnabled(True)

    def wideSet(self):
        if not self.cp2130Thread.setWideIn(self.wideCheck.isChecked()):
            self.wideCheck.toggle()

    # called everytime process thread emits new data
    @pyqtSlot(list, int)
    def plotDataReady(self,data, samples):
        #EMG
        self.plotTime = self.plotTime[len(data):]
        self.plotTime.extend(list(range(self.plotTime[-1]+1,self.plotTime[-1]+1+len(data))))
        if self.scrollStyle.currentIndex() == 0:
            self.plotXPlace = (self.plotXPlace + len(data))%self.xRange
            for i in range(0, self.numPlots):
                self.plotScrollData[i] = self.plotScrollData[i][len(data):]
                self.plotScrollData[i].extend([sample[self.plotCh[i].value()] for sample in data])
                self.plotLineRefs[i].setData(x=self.plotTime,y=self.plotScrollData[i])
        elif self.scrollStyle.currentIndex() == 1:
            for i in range(0,len(data)):
                for ch in range(0,self.numPlots):
                    self.plotPlaceData[ch][self.plotXPlace] = data[i][self.plotCh[ch].value()]
                self.plotXPlace += 1
                if self.plotXPlace == self.xRange:
                    self.plotXPlace = 0
            for ch in range(0,self.numPlots):
                self.plotLineRefs[ch].setData(y=self.plotPlaceData[ch])

        #FORCE SENSOR
        if 'Hold rest position for' in self.messageList[self.messageIdx]:
            self.forceCalibration.append(np.mean(data[-1][32:37]))
            self.forceMinimum = int(np.mean(self.forceCalibration))
            self.effort.setMinimum(self.forceMinimum)
        elif 'Start squeezing as hard as you can for' in self.messageList[self.messageIdx]:
            self.forceCalibration = []
        elif 'Hold squeezing as hard as you can for' in self.messageList[self.messageIdx]:
            self.forceCalibration.append(np.mean(data[-1][32:37]))
            self.forceMaximum = int(np.mean(self.forceCalibration))
            self.effort.setMaximum(self.forceMaximum)
        elif "Sqeeze\nApply force to the bottle and match the" in self.messageList[self.messageIdx]:
            if self.firstInitialSample:
                self.initialSample = samples
                self.firstInitialSample = False
                self.processThread.appendEmptyRow(0)
            x = self.plotEffortControlled(samples)
            self.effortControlled.setValue(x)
            if self.firstInitialSample:
                self.effortControlled.setValue(self.effortControlled.minimum())
                self.effort.setValue(self.effortControlled.minimum())
                self.processThread.appendEmptyRow(-1)
        self.effort.setValue(np.mean(data[-1][32:37]))

    def plotEffortControlled(self, samples):
        totalTime = self.gestureLen.value()*1000
        currentTime = samples-self.initialSample
        x = (currentTime*self.effortControlled.maximum())/totalTime
        if currentTime==totalTime-50:
            self.firstInitialSample = True
        return x

    # called every second (1000 samples) by the process thread
    @pyqtSlot()
    def messageTick(self):
        if self.expCheck.isChecked():
            if self.messageIdx < self.numMessages-1:
                self.messageIdx += 1
            self.message.setText(self.messageList[self.messageIdx])
            self.posImage.setPixmap(QPixmap(self.imageDir + self.posImageList[self.messageIdx] + ".png").scaledToWidth(int(self.posImage.geometry().width())))

    def closeEvent(self, event):
        self.cp2130Thread.quit()
        self.processThread.quit()

if __name__ == "__main__":
    # raw samples read from base station
    sampleQueue = Queue()

    # open connection to cp2130 base station
    cp2130Handle, kernelAttached, deviceList, context = flexemgComm.open_cp2130()

    # test for connection with board
    if not(flexemgComm.writeReg(cp2130Handle,0,0x0F,0xBEEF)):
        # quit program if no connection found
        print('Could not find FlexEMG board, exiting!')
    else:
        print('Successfully connected to FlexEMG board!')
        # main application instance
        QApplication.setStyle('fusion')
        app = QApplication([])
        QApplication.setStyle('fusion')
        # force the style to be the same on all OSs:
        app.setStyle("fusion")

        # Now use a palette to switch to dark colors:
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        app.setPalette(palette)

        # set up things here before starting event loop
        window = MainWindow()
        window.resize(1450, 772)
        window.show()

        # start event loop
        ret = app.exec_()

        # reach here after closing window
        window.cp2130Thread.quit()
        window.processThread.quit()
        del window
        sys.exit(ret)
