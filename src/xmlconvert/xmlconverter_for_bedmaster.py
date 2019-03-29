"""
This modules provides XML conversion to Bin file
"""
import os
from pathlib import Path
import datetime
from dateutil import parser
import random
from .xml2bin_state import Xml2BinState
from .fixsampling import fixsamplingarr
from myutil import parsetime
from myutil import dtTimestampFormat
from myutil import getOutputFilename
from binfilepy import BinFile
from binfilepy import CFWBINARY
from binfilepy import CFWBCHANNEL
from binfilepy import constant
import xml.etree.ElementTree as ET
import base64
from array import array
import numpy as np
import math
from typing import List
from typing import Dict
from typing import Any


class XmlConverterForBedMaster:
    """
    This class provides XML conversion to Bin file
    """
    outputDir = ""
    outputFnExt = ""
    outputFnPattern = ""
    defaultSamplesPerSec = 0
    channelPatternList = None
    channelInfoList = None
    ignoreGap = False
    ignoreGapBetweenSegs = False
    warningOnGaps = False
    outputFnTimeFormatDict = None
    header = None
    headerStartDt = None
    channels = []
    name2Channel = {}
    outputFileSet = set()
    outputFileList = []

    def __init__(self, outputDir: str = "", outputFnPattern: str = "", outputFnExt: str = "", defaultSamplesPerSec: int = 0,
                 channelPatternList: List = None, channelInfoList: List = None,
                 ignoreGap: bool = False, ignoreGapBetweenSegs: bool = False, warningOnGaps: bool = False,
                 outputFnTimeFormatDict: Dict = None):
        self.outputDir = outputDir
        self.outputFnPattern = outputFnPattern
        self.outputFnExt = outputFnExt
        self.defaultSamplesPerSec = defaultSamplesPerSec
        self.channelPatternList = channelPatternList
        self.channelInfoList = channelInfoList
        self.ignoreGap = ignoreGap
        self.ignoreGapBetweenSegs = ignoreGapBetweenSegs
        self.warningOnGaps = warningOnGaps
        self.outputFnTimeFormatDict = outputFnTimeFormatDict

    def clearState(self):
        self.header = None
        self.headerStartDt = None
        self.channels = []
        self.name2Channel = {}

    def clearOutputFileList(self):
        self.outputFileSet = set()
        self.outputFileList = []

    def setDefaultSamplesPerSec(self, defaultSamplesPerSec: int):
        self.defaultSamplesPerSec = defaultSamplesPerSec

    def setChannelPatternList(self, channelPatternList: List):
        self.channelPatternList = channelPatternList

    def inChannelPatternList(self, label: str):
        if (self.channelPatternList is None) or (len(self.channelPatternList) == 0):
            return True
        for p in self.channelPatternList:
            if p.match(label) is not None:
                return True
        return False

    def getChannelInfo(self, label: str):
        if self.channelInfoList is not None:
            for cSettingInfo in self.channelInfoList:
                labelPattern = cSettingInfo.get("labelPattern")
                if labelPattern.match(label) is not None:
                    return cSettingInfo
        return None

    def renameOutputFnWithEndtime(self, numSamples: int, tagsDict: Dict, x: Xml2BinState, filename: str):
        # rename the file that we just closed (if filename pattern has {endtime})
        if "{endtime}" in self.outputFnPattern:
            fmt = self.outputFnTimeFormatDict.get("starttime", None) if (self.outputFnTimeFormatDict is not None) else None
            tagsDict["starttime"] = dtTimestampFormat(self.headerStartDt, fmt)
            fmt = self.outputFnTimeFormatDict.get("exetime", None) if (self.outputFnTimeFormatDict is not None) else None
            tagsDict["exetime"] = dtTimestampFormat(x.timestampTm, fmt)
            endDt = self.headerStartDt + datetime.timedelta(seconds=int(numSamples / self.defaultSamplesPerSec))
            fmt = self.outputFnTimeFormatDict.get("endtime", None) if (self.outputFnTimeFormatDict is not None) else None
            tagsDict["endtime"] = dtTimestampFormat(endDt, fmt)
            if "tempendtime" in filename:
                original_filename = filename
                filename = getOutputFilename(self.outputDir, self.outputFnPattern, tagsDict, self.outputFnExt)
                if original_filename != filename:
                    os.rename(original_filename, filename)
                x.lastBinFilename = filename
            # end-if
        # end-if

    # return total number of samples written
    def convert(self, xmlFile: str, tagsDict: Dict, x: Xml2BinState, print_processing_fn: bool = False):
        """
        convert to BIN file from XML
        """
        binFileOut = None
        filename = ""
        chanLabel = []
        tempChanInfo = []
        tempChanLabel = []
        tempChanLabel2Index = {}
        startWaveTm = datetime.datetime.min
        numSamples = 0
        totalNumSamplesWritten = 0
        firstBinFile = True
        firstMeasurement = True

        infilePath = Path(xmlFile)
        if not infilePath.exists():
            raise XmlConverterError("Cannot open file: {0}".format(xmlFile))
        else:
            if len(x.lastBinFilename) > 0:
                binFileOut = BinFile(x.lastBinFilename, "r+")
                binFileOut.open()
                binFileOut.readHeader()
                filename = x.lastBinFilename
                firstBinFile = False
                # copy BIN file's channel info
                chanLabel = []
                tempChanLabel = []
                chanLabel2Index = {}
                tempChanLabel2Index = {}
                idx = 0
                for c in binFileOut.channels:
                    chanLabel.append(c.Title)
                    chanLabel2Index[c.Title] = idx
                    tempChanLabel.append(c.Title)
                    tempChanLabel2Index[c.Title] = idx
                    idx += 1
                self.header = binFileOut.header
                second = int(math.floor(self.header.Second))
                microsecond = int((self.header.Second - math.floor(self.header.Second)) * 100)
                self.headerStartDt = datetime.datetime(self.header.Year, self.header.Month, self.header.Day,
                                                       self.header.Hour, self.header.Minute, second, microsecond)
                numSamples = self.header.SamplesPerChannel
            # end-if len(x.lastBinFilename)
            if print_processing_fn:
                print("Processing XML file: {0}".format(infilePath.name))
            tree = ET.parse(xmlFile)
            root = tree.getroot()
            # print("root tag = {0}".format(root.tag))
            if root.tag == "BedMasterEx":
                for child1 in root:
                    if child1.tag == "Segment":
                        for child3 in child1:
                            if child3.tag == "Waveforms":
                                collectionTime, collectionTimeUTC = self.processWaveforms(child3)
                                collectionTimeDt = parsetime(collectionTime)
                                tempChanInfo = []
                                tempChanLabel = []
                                tempChanLabel2Index = {}
                                if self.header is None:
                                    self.headerStartDt = collectionTimeDt
                                    self.header = CFWBINARY()
                                    self.header.setValue(1.0 / self.defaultSamplesPerSec, collectionTimeDt.year, collectionTimeDt.month,
                                                         collectionTimeDt.day, collectionTimeDt.hour, collectionTimeDt.minute, collectionTimeDt.second, 0, 0)
                                # print(collectionTime, collectionTimeUTC)
                                idx = 0
                                for child4 in child3:
                                    if (child4.tag == "WaveformData"):
                                        ID, channel, hz, points, uom, wave = self.processWaveformData(child4)
                                        if self.inChannelPatternList(channel):
                                            # print(channel, wave, points, pointsBytes, min_, max_, offset, gain, hz)
                                            wavedata = self.decodeWave(wave)
                                            # print(wavedata)
                                            if self.defaultSamplesPerSec != hz:
                                                wavedata = fixsamplingarr(wavedata, hz, self.defaultSamplesPerSec)
                                            tempChanInfo.append({"label": channel, "data": wavedata, "points": points, "hz": hz, "uom": uom})
                                            tempChanLabel.append(channel)
                                            tempChanLabel2Index[channel] = idx
                                            idx += 1
                                # end-for child4
                                if (firstBinFile is True) or (len(tempChanLabel) > 0 and self.channelChanged(chanLabel, tempChanLabel)):
                                    if firstBinFile is False:
                                        binFileOut.closeFile()
                                        binFileOut = None
                                        # rename the file that we just closed (if filename pattern has {endtime})
                                        self.renameOutputFnWithEndtime(numSamples, tagsDict, x, filename)
                                        if not (x.lastBinFilename in self.outputFileSet):
                                            self.outputFileSet.add(x.lastBinFilename)
                                            self.outputFileList.append(x.lastBinFilename)
                                        # reset new headerStartDt
                                        self.headerStartDt = collectionTimeDt
                                        self.header.setValue(1.0 / self.defaultSamplesPerSec, collectionTimeDt.year, collectionTimeDt.month,
                                                             collectionTimeDt.day, collectionTimeDt.hour, collectionTimeDt.minute, collectionTimeDt.second, 0, 0)
                                    firstBinFile = False
                                    self.header.NChannels = len(tempChanInfo)
                                    fmt = self.outputFnTimeFormatDict.get("starttime", None) if (self.outputFnTimeFormatDict is not None) else None
                                    tagsDict["starttime"] = dtTimestampFormat(self.headerStartDt, fmt)
                                    fmt = self.outputFnTimeFormatDict.get("exetime", None) if (self.outputFnTimeFormatDict is not None) else None
                                    tagsDict["exetime"] = dtTimestampFormat(x.timestampTm, fmt)
                                    # we do not know the end at this point
                                    tagsDict["endtime"] = "tempendtime" + str(random.randint(10000, 100000))
                                    filename = getOutputFilename(self.outputDir, self.outputFnPattern, tagsDict, self.outputFnExt)
                                    x.lastBinFilename = filename
                                    binFileOut = BinFile(filename, "w")
                                    binFileOut.open()
                                    binFileOut.setHeader(self.header)
                                    chanData = []
                                    chanLabel = []
                                    for cinfo in tempChanInfo:
                                        label = cinfo["label"]
                                        cSettingInfo = self.getChannelInfo(label)
                                        if cSettingInfo is not None:
                                            uom = cSettingInfo.get("uom", cinfo.get("uom", ""))
                                            rangeLow = cSettingInfo.get("rangeLow", 0)
                                            rangeHigh = cSettingInfo.get("rangeHigh", 100)
                                            offset = cSettingInfo.get("offset", 0)
                                            scale = cSettingInfo.get("scale", 1)
                                        else:
                                            uom = cinfo.get("uom", "")
                                            rangeLow = 0
                                            rangeHigh = 100
                                            offset = 0
                                            scale = 1
                                        channel = CFWBCHANNEL()
                                        channel.setValue(label, uom, scale, offset, rangeLow, rangeHigh)
                                        binFileOut.addChannel(channel)
                                        chanData.append(cinfo["data"])
                                        chanLabel.append(label)
                                    binFileOut.writeHeader()
                                    firstMeasurement = False
                                    numSamples = binFileOut.writeChannelData(chanData)
                                    totalNumSamplesWritten += numSamples
                                    binFileOut.updateSamplesPerChannel(numSamples, True)
                                elif len(tempChanInfo) > 0:
                                    chanData = []
                                    chanLabel = []
                                    for cinfo in tempChanInfo:
                                        label = cinfo["label"]
                                        chanData.append(cinfo["data"])
                                        chanLabel.append(label)
                                    # gap handling
                                    endDt = self.headerStartDt + datetime.timedelta(seconds=int(numSamples / self.defaultSamplesPerSec))
                                    gap = collectionTimeDt - endDt
                                    actualGapInSec = int(gap.total_seconds())
                                    if (not self.ignoreGapBetweenSegs) and firstMeasurement:
                                        gapInSec = actualGapInSec
                                    elif not self.ignoreGap:
                                        gapInSec = actualGapInSec
                                    else:
                                        gapInSec = 0
                                    if self.warningOnGaps and (gapInSec != 0):
                                        print("Waveforms CollectionTime: {0} shows gap (or overlap) = {1} secs".format(collectionTime, gapInSec))
                                    firstMeasurement = False
                                    numSamplesWritten = binFileOut.writeChannelData(chanData, self.defaultSamplesPerSec, gapInSec)
                                    totalNumSamplesWritten += numSamplesWritten
                                    numSamples = numSamplesWritten + binFileOut.header.SamplesPerChannel
                                    binFileOut.header.SamplesPerChannel = numSamples
                                    self.header.SamplesPerChannel = numSamples
                                    binFileOut.updateSamplesPerChannel(numSamples, True)
                                # end-if firstBinFile
                            # end-if "measurement"
                # end-for child1
            # end-if root
        # end-if
        if binFileOut is not None:
            binFileOut.closeFile()
            binFileOut = None
            # rename the file that we just closed (if filename pattern has {endtime})
            self.renameOutputFnWithEndtime(numSamples, tagsDict, x, filename)
            if not (x.lastBinFilename in self.outputFileSet):
                self.outputFileSet.add(x.lastBinFilename)
                self.outputFileList.append(x.lastBinFilename)
        return totalNumSamplesWritten

    def renameChannels(self, print_rename_details: bool = False):
        # progress
        # need to support renaming of channel label
        # also, support use of Regex for channel label, and renameTo expression
        hasRenameTo = False
        for cSettingInfo in self.channelInfoList:
            renameTo = cSettingInfo.get("renameTo", "")
            if len(renameTo) > 0:
                hasRenameTo = True
                break
            # end-if
        # end-for
        if hasRenameTo:
            print("Renaming channels in output files...")
            numFilesChanged = 0
            for fn in self.outputFileList:
                print("processing {0}...".format(Path(fn).name))
                updatedFile = False
                with BinFile(fn, "r+") as f:
                    f.readHeader()
                    for c in f.channels:
                        cSettingInfo = self.getChannelInfo(c.Title)
                        if cSettingInfo is not None:
                            newLabel = cSettingInfo.get("renameTo", "")
                            if len(newLabel) > 0:
                                filename = Path(fn).name
                                oldLabel = c.Title
                                c.Title = newLabel
                                updatedFile = True
                                if print_rename_details:
                                    print("{0} file: {1} -> {2}".format(filename, oldLabel, newLabel))
                    if updatedFile:
                        f.writeHeader()
                        numFilesChanged += 1
                # end-with
            # end-for
            if numFilesChanged == 0:
                if print_rename_details:
                    print("No output files's channel labels need to be changed.")
        # end-if

    def processWaveforms(self, e: object):
        collectionTime = e.attrib.get("CollectionTime", "")
        collectionTimeUTC = e.attrib.get("CollectionTimeUTC", "")
        return collectionTime, collectionTimeUTC

    def processWaveformData(self, e: object):
        ID = int(e.attrib.get("ID", ""))
        channel = e.attrib.get("Label", "")
        hz = float(e.attrib.get("SampleRate", ""))
        points = int(e.attrib.get("Samples", ""))
        uom = e.attrib.get("UOM", "")
        wave = e.text
        return ID, channel, hz, points, uom, wave

    def decodeWave(self, x: str):
        val_list = x.split(",")
        a = array("h", (0,) * len(val_list))
        i = 0
        for v in val_list:
            a[i] = int(v)
            i += 1
        return a

    def moveTempChanLabel(self, chanLabelArr: List[str], tempChanLabelArr: List[str]):
        chanLabelArr.clear()
        for l in tempChanLabelArr:
            chanLabelArr.append(l)

    def channelChanged(self, chanLabelArr: List[str], tempChanLabelArr: List[str]):
        changed = False
        if len(chanLabelArr) != len(tempChanLabelArr):
            changed = True
        else:
            for l, t in zip(chanLabelArr, tempChanLabelArr):
                if l != t:
                    changed = True
                    break
        return changed
