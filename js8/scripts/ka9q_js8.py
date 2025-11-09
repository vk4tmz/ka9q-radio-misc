#!/usr/bin/env python

################################################################################
##
## ./ka9q-ks8-recorder.py <command> -f <freq> -sm <submode> -d <data dir> -ma <mcast_addr> 
##
##    command    - start | stop | status
##    freq       - defaults to all, but allow you specify 1 or more subset of fequencies in kHz.
##    submode    - defaults to all, but allow you specify 1 or more subset (eg turbo, fast, norm, slow)
##    data_dir   - top level folder for data files / folder to be located.
##    mcast_addr - defaults: "js8-pcm.local"
##
################################################################################


import argparse
from filelock import FileLock
import json
import logging
import psutil
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from aprsis_reporter import APRSReporter, DEFAULT_APRS_PORT, DEFAULT_APRS_HOST
from datetime import datetime, timezone
from pathlib import Path
from ka9q_js8Parser import Js8Parser
from ka9q_js8Utils import logError, isEmpty, findFile, truncateFile, \
        archiveFile, writeStringsToFile, writeStringToFile, appendJson, loadJson, \
        ARCHIVE_METHOD_MOVE, ARCHIVE_METHOD_TRUNCATE

DEFAULT_DATA_DIR="./data"
DEFAULT_MCAST_ADDR="js8-pcm.local"
DEFAULT_SPOT_LOG="/var/log/js8.log"

PCMRECORD_BIN = "/usr/local/bin/pcmrecord"
JS8_BIN="/usr/bin/js8"

SM_TURBO = {'name': "turbo", 'code': "C", "duration": 6}
SM_FAST  = {'name': "fast", 'code': "B", "duration": 10}
SM_NORM  = {'name': "norm", 'code': "A", "duration": 15} 
SM_SLOW  = {'name': "slow", 'code': "E", "duration": 30}

SUBMODES_BYNAME = [SM_TURBO["name"], SM_FAST["name"], SM_NORM["name"], SM_SLOW["name"]]

SUBMODES = [SM_TURBO, SM_FAST, SM_NORM, SM_SLOW]

SUBMODES_LOOKUP = {
    "turbo": SM_TURBO, 
    "fast": SM_FAST, 
    "norm": SM_NORM, 
    "slow": SM_SLOW,
}

FREQ_LIST=[1842, 3578, 7078, 10130, 14078, 18104, 21078, 24922, 28078, 27246]
# SSRC autogen can/will eventually vary from actualy freq_khz (ie 17m clashes with FT8/FT4)
FREQ_SSRC=[1842, 3578, 7078, 10130, 14078, 18104, 21078, 24922, 28078, 27246]

data_dir = DEFAULT_DATA_DIR

DEFAULT_DECODE_DEPTH = 3

APRSIS_CMD_REX = r"(?P<callsign>[\w\a/]+): @APRSIS ((GRID\s)?(?P<grid>[\w\d]+)$)?((CMD\s)?(?P<cmd_msg>:[@\-\.\d\w]+[ ]+:[@\-\.\d\w]+[ ]+.*$))?"

# Configure basic logging to a file and the console
logging.basicConfig(
    level=logging.INFO,  # Set the minimum logging level to INFO
#    level=logging.DEBUG,  # Set the minimum logging level to INFO    
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("ka9q-js8.log"),  # Log to a file
        logging.StreamHandler()  # Log to the console (standard output)
    ]
)

glogger = logging.getLogger(__name__)


############################################################################

class ModeConfig:

    spot_log_fn:str

    freq_list: int
    submode: str
    data_dir:str
    mcast_addr:str

    mode_root_dir:str
    mode_rec_dir:str
    mode_rec_error_dir:str
    mode_rec_proc_dir:str
    mode_data_dir:str
    mode_dec_dir:str
    mode_dec_error_dir:str
    mode_dec_proc_dir:str
    mode_tmp_dir:str


    def __init__(self, freq_khz:int, submode:str, data_dir: str=DEFAULT_DATA_DIR, mcast_addr:str=DEFAULT_MCAST_ADDR, spot_log_fn:str=DEFAULT_SPOT_LOG):

        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))

        self.freq_khz = freq_khz
        self.freq_hz = freq_khz * 1000
        self.submode = submode
        self.data_dir = data_dir
        self.mcast_addr = mcast_addr
        self.spot_log_fn = spot_log_fn

        self.setupSubmodeFolders(freq_khz, submode["name"])


    def setupSubmodeFolders(self, freq_khz:int, mode:str):
                
        freq_hz = freq_khz * 1000
    
        self.mode_root_dir = f"{self.data_dir}/{freq_hz}/{mode}" 
        self.mode_rec_dir = f"{self.mode_root_dir}/rec"
        self.mode_rec_error_dir = f"{self.mode_rec_dir}/error"
        self.mode_rec_proc_dir = f"{self.mode_rec_dir}/done"
        self.mode_data_dir = f"{self.mode_root_dir}/data"
        self.mode_dec_dir = f"{self.mode_root_dir}/decode"
        self.mode_dec_error_dir = f"{self.mode_dec_dir}/error"
        self.mode_dec_proc_dir = f"{self.mode_dec_dir}/done"
        self.mode_tmp_dir = f"{self.mode_root_dir}/tmp"

        Path(self.mode_rec_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_rec_error_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_rec_proc_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_dec_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_dec_error_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_dec_proc_dir).mkdir(parents=True, exist_ok=True)
        Path(self.mode_tmp_dir).mkdir(parents=True, exist_ok=True)


#################################################################################
# Js8Recorder Class
#################################################################################

class Js8Recorder:
    mode_conf: ModeConfig = None
    recorder_pids_file = None

    def __init__(self, mode_conf: ModeConfig):
        self.mode_conf = mode_conf
        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))

    def start(self):

        now_utc = datetime.now(timezone.utc)

        freq_khz = self.mode_conf.freq_khz
        freq_hz = freq_khz * 1000
        mode = self.mode_conf.submode['name']

        rec = {
            "freq_khz": freq_khz,
            "freq_hz": freq_khz * 1000,
            "submode": mode,
            "submode_duration": self.mode_conf.submode["duration"],
            "mcast_addr": self.mode_conf.mcast_addr,
            "pid": None,
            # now_utc.isoformat() or Epoch/Timestamp ?
            "timestamp": int(now_utc.timestamp()),
            "ret_code": None
        }

        self.logger.info(f"Starting new pcmrecord process for Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}]")

        # Determine SSRC
        freq_idx = FREQ_LIST.index(freq_khz)
        freq_ssrc = FREQ_SSRC[freq_idx]
        self.logger.info(f"Selected SSRC: [{freq_ssrc}] for Freq: [{freq_khz}]")

        # IMPORTANT - USE Scott's WSPRDaemon version of "pcmrecord" to ensure that based on -L  will start correct time.
        cmd = [PCMRECORD_BIN, 
            "-L", str(self.mode_conf.submode["duration"]), 
            "-d", self.mode_conf.mode_rec_dir, 
            "-W", 
            "-S", 
            str(freq_ssrc), 
            "--jt", 
            self.mode_conf.mcast_addr]

        logfile = open(f"{self.mode_conf.mode_data_dir}/pcmrecord.log", "w")

        # Start the process in a new session, detaching it from the current terminal
        process = subprocess.Popen(cmd, start_new_session=True,
                                #    stdout=subprocess.PIPE, 
                                #    stderr=subprocess.PIPE)                               
                                stdout=logfile, 
                                stderr=logfile)
        
        rec["pid"] = process.pid
        rec["ret_code"] = process.returncode

        if (process.returncode and (process.returncode != 0)):
            self.logger.error(f"Command failed with return code: {process.returncode}")

        return rec;


#################################################################################
# Js8FrameProcessor Class
#################################################################################

class Js8FrameProcessor:

    aprsReporter: APRSReporter

    callsigns = {}
    msgByFreq = {}
    msgByFreq_incomplete = {}

    def __init__(self, aprsReporter:APRSReporter):
        self.aprsReporter = aprsReporter
        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))

    def archiveExpired(self):
        # Move "expired" activity over to msgByFreq_incomplete to keep our callsign history clean
        for dial_freq in self.msgByFreq.keys():

            msgs = self.msgByFreq[dial_freq]
            msgs_copy = self.msgByFreq[dial_freq][:]

            incomp_msgs = self.msgByFreq_incomplete[dial_freq]

            # Iterate throug ah copy of the msgs, move the activity over to msgByFreq_incomplete
            for act_rec in msgs_copy:
                if act_rec["is_expired"]:
                     incomp_msgs.append(act_rec)
                     msgs.remove(act_rec)

    def cleanup(self):
        self.archiveExpired()

    def reportCommandMessageAPRSIS(self, callsign:str, msg:str):
        if (isEmpty(callsign)):
            self.logger.error(f"APRIS position report request contains empty Callsign.")
            return
        
        if (isEmpty(msg)):
            self.logger.error(f"APRIS position report request contains empty Message.")
            return

        if (self.aprsReporter is not None):
            self.aprsReporter.reportAprsMessage(callsign, msg)


    def reportPositionAPRSIS(self, aprs_callsign:str, aprs_grid:str, freq_mhz: float, snr: int):
        if (isEmpty(aprs_callsign)):
            self.logger.error(f"APRIS position report request contains empty Callsign.")
            return

        if (isEmpty(aprs_grid)):
            self.logger.error(f"APRIS position report request for Callsign: [{aprs_callsign}] has an invalid locator: [{aprs_grid}]")
            return

        comment = f"JS8 {aprs_callsign} {freq_mhz:.06f}MHz {snr:+03d}dB"
        if (self.aprsReporter is not None):
            self.aprsReporter.reportAprsPosition(aprs_callsign, aprs_grid, comment)


    def processAPRSIS(self, act_rec: dict):

        # callsign = act_rec["callsign"]
        # locator = act_rec["locator"]
        freq_hz = act_rec["freq"]
        freq_mhz = freq_hz/1000000
        msg = act_rec["full_msg"]
        snr = int(act_rec["snr"])

        if (self.aprsReporter is None):
            self.logger.warning(f"Received APRIS message: [{msg}], skipping as APRSIS reporting is not enabled.")
            return

        # Handles following possbile
        #  - <callsign> @APRIS ([[GRID] <grid>] | [[CMD] :<cs_from> :<cs_to>]
        match = re.match(APRSIS_CMD_REX, msg)

        if (match is None):
            self.logger.error(f"Malformed APRSIS message: [{msg}]")

        callsign = match.group("callsign")
        grid = match.group("grid")
        cmd_msg = match.group("cmd_msg")

        if (not isEmpty(callsign) and not isEmpty(grid)):
            callsign = match.group("callsign")
            grid = match.group("grid")
            self.reportPositionAPRSIS(callsign, grid, freq_mhz, snr)

        elif (not isEmpty(callsign) and not isEmpty(cmd_msg)):
            self.reportCommandMessageAPRSIS(callsign, cmd_msg)

        else:
            self.logger.error(f"Invalid @APRIS message: [{msg}] - skipped.")

    def getOrCreateDict(self, dd:dict, key:str) -> dict:
        if (key in dd):
            return dd[key]
        else:
            new_rec = {}
            dd[key] = new_rec
            return new_rec
        
    def getOrCreateList(self, dd:dict, key:str) -> list:
        if (key in dd):
            return dd[key]
        else:
            new_list = []
            dd[key] = new_list
            return new_list    
        

    def addActivityByDateTimeFreq(self, cs_rec, act_rec):
        # <Callsig>::<YYYY-MM-DD>::<HH>::<DIAL_FREQ>
        act_dt = datetime.fromtimestamp(act_rec["timestamp"], tz=timezone.utc)
        dt_YMD = act_dt.strftime("%Y-%m-%d")
        dt_H = act_dt.strftime("%H")
        dial_freq = act_rec["dial_freq"]

        dtYMD_recs = self.getOrCreateDict(cs_rec["activity_YMD"], dt_YMD)
        dtH_recs = self.getOrCreateDict(dtYMD_recs, dt_H)
        band_recs = self.getOrCreateList(dtH_recs, dial_freq)

        band_recs.append(act_rec)
        

    def processFrame(self, dec: dict):
        dial_freq = dec["dial_freq"]
        offset = dec["offset"]

        if dec["is_valid"]:
            
            if (dial_freq not in self.msgByFreq):
                band_act_recs = []
                self.msgByFreq[dial_freq] = band_act_recs
                self.msgByFreq_incomplete[dial_freq] = []
            else:
                band_act_recs = self.msgByFreq[dial_freq]

            #scan for activity +/-10hz
            act_rec = None
            for rec in band_act_recs:
                
                # I know the JS8 spec will print traffic +/-10Hz, but for now assume as +/- 3 as we are performing avergaing so should track
                # BW=10
                BW=3
                # TODO: Do I need to filter / be "mode" specific aswell ??
                if (((rec["offset"] - BW) <= offset <= (rec["offset"] + BW)) and 
                    ((abs(dec["timestamp"] - rec["first_ts"]) <= 60) or (abs(dec["timestamp"] - rec["last_ts"]) <= 60))):
                    act_rec = rec

                    # TODO: any more smarts to consider here ?
                    break

                else:
                    # lets see if incomplete activity which has not seen a "start" (seen_first) frame has expired. If it has mark it expired and will be purged later
                    if (((not rec["seen_first"]) or (not rec["seen_last"])) and 
                        (not rec["is_complete"]) and 
                        (not rec["is_expired"]) and 
                        (abs(rec["last_ts"] - dec["timestamp"]) > 60)):
                        rec["is_expired"] = True


            if act_rec is None:
                # TODO: Review if seend (first/last) needed here yet.
                # offset will be a running average 
                act_rec = {"offset": offset, "first_ts": dec["timestamp"], "last_ts": dec["timestamp"],
                           "seen_first": False, "seen_last": False, "offset_total": offset, 
                           "is_complete": False, "is_expired": False, 
                           "id": str(uuid.uuid4()), "msgs": [dec],
                           # These will be populated upon "completeness"
                           "timestamp": None,"callsign": None, "locator": None, "freq": None, "full_msg": None, "snr": None}
                band_act_recs.append(act_rec)                            
                
            else:

                act_rec["msgs"].append(dec)
                # Handle if msgs out of order during processing
                if (dec["timestamp"] < act_rec["first_ts"]):
                    act_rec["first_ts"] = dec["timestamp"]
                if (dec["timestamp"] > act_rec["last_ts"]):
                    act_rec["last_ts"] = dec["timestamp"]

                act_rec["offset_total"] += dec["offset"]
                act_rec["offset"] = int(act_rec["offset_total"] / len(act_rec["msgs"]))

            # Check if we have just encounter first / last / only expected frame
            frame_class = dec["frame_class"]
            thread_type = int(dec["thread_type"])

            if ((not act_rec["is_complete"]) and (not act_rec["is_expired"])):

                ## Single Js8FrameDirected Frames
                if ((frame_class in ["Js8FrameDirected", "Js8FrameHeartbeat"])
                    and (thread_type == 3)):
                    act_rec["seen_first"] = True
                    act_rec["seen_last"] = True
                    act_rec["is_complete"] = True
                    if (act_rec["locator"] is None) and (dec["locator"] is not None):
                        act_rec["locator"] = dec["locator"]


                # Js8FrameDirected / Js8FrameDataCompressed
                elif ((frame_class == "Js8FrameDirected") and (thread_type == 1)):
                    act_rec["seen_first"] = True
                elif ((frame_class == "Js8FrameDataCompressed") and (thread_type == 0)):
                    # in the middle 
                    pass
                elif ((frame_class == "Js8FrameDataCompressed") and (thread_type == 2)):
                    act_rec["seen_last"] = True
                    if (act_rec["seen_first"]):
                        act_rec["is_complete"] = True
                
                # Js8FrameDirected / Js8FrameData
                elif ((frame_class == "Js8FrameData") and (thread_type == 0)):
                    # in the middle 
                    pass
                elif ((frame_class == "Js8FrameData") and (thread_type == 2)):
                    act_rec["seen_last"] = True
                    if (act_rec["seen_first"]):
                        act_rec["is_complete"] = True

                ## Js8FrameCompound / Js8FrameCompoundDirected and Js8FrameCompoundCompressed
                elif ((frame_class == "Js8FrameCompound") and (thread_type == 1)):
                    act_rec["seen_first"] = True
                    if (act_rec["locator"] is None) and (dec["locator"] is not None):
                        act_rec["locator"] = dec["locator"]
                elif ((frame_class == "Js8FrameCompoundDirected") and (thread_type == 0)):
                    # in the middle 
                    pass
                elif ((frame_class == "Js8FrameCompoundDirected") and (thread_type == 2)):
                    act_rec["seen_last"] = True
                    if (act_rec["seen_first"]):
                        act_rec["is_complete"] = True
                
                else:
                    # TODO: Review and confirm 
                    dec["is_valid"] = False
                    ve = dec["validation_errors"]
                    ve["unexepected_frame"] = True

                #
                # If now complete do the following
                #   TODO: if complete should we remove all dec that are "invalid" ?
                if act_rec["is_complete"]:
                    full_msg = ""
                    callsign = None
                    timestamp = None
                    snr = None
                    prev_thread_type = None
                    for msg in act_rec["msgs"]:
                        if (msg["is_valid"]):
                            l_thread_type = msg["thread_type"]
                            l_frame_class = msg["frame_class"]
                            # concat all valid messages with a space
                            if (msg["msg"] is not None):
                                full_msg = full_msg + msg["msg"]
                                if (l_frame_class in ["Js8FrameCompound","Js8FrameCompoundDirected"]):
                                    full_msg = full_msg + " "

                            # Grab first valid callsign
                            if (msg["callsign"] is not None) and (callsign is None):
                                callsign = msg["callsign"]

                            # Grab first valid timestamp
                            if (timestamp is None and msg["timestamp"] is not None):
                                timestamp = msg["timestamp"]

                            if (snr is None and msg["db"] is not None):
                                snr = msg["db"]

                            prev_thread_type = thread_type


                    act_rec["callsign"] = callsign
                    act_rec["full_msg"] = full_msg
                    act_rec["timestamp"] = timestamp
                    act_rec["snr"] = snr
                    act_rec["dial_freq"] = dial_freq
                    act_rec["freq"] = dial_freq + act_rec["offset"]

                    # Add / link to callsign_db
                    if callsign in self.callsigns:
                        cs_rec = self.callsigns[callsign]
                        if (act_rec["timestamp"] > cs_rec["last_ts"]):
                            cs_rec["last_ts"] = act_rec["timestamp"]

                    else:
                        #cs_rec = {"last_freq": (dial_freq+act_rec["offset"]), "last_ts": timestamp, "activity": []}
                        cs_rec = {"last_freq": dial_freq, "first_ts": timestamp, "last_ts": timestamp, "activity": [], "activity_YMD": {}}
                        self.callsigns[callsign] = cs_rec

                    cs_rec["activity"].append(act_rec)
                    self.addActivityByDateTimeFreq(cs_rec, act_rec)
                    

                    # Process JS8 "@" Commands
                    if ("@APRSIS" in act_rec["full_msg"]):
                        self.processAPRSIS(act_rec)
                        


            # else:
            #     # TODO: Review and confirm 
            #     dec["is_valid"] = False
            #     ve = dec["validation_errors"]
            #     ve["unexepected_frame_after_end"] = True
            

            # cs_rec = callsigns[]
            # callsigns.append(cs_rec)  


#################################################################################
# Js8Decoder Class
#################################################################################

class Js8Decoder:
    mode_conf: ModeConfig = None
    js8Parser: Js8Parser = None
    js8FrameProc: Js8FrameProcessor = None

    def __init__(self, mode_conf: ModeConfig, aprsReporter:APRSReporter):
        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))
        self.mode_conf = mode_conf
        self.js8Parser = Js8Parser(self.mode_conf.freq_khz, "usb")
        self.js8FrameProc = Js8FrameProcessor(aprsReporter)

    def decoding_process(self):

        now_utc = datetime.now(timezone.utc)

        freq_khz = self.mode_conf.freq_khz
        mode = self.mode_conf.submode['name']

        self.logger.info(f"Starting js8Decoder process for Freq: [{freq_khz}] kHz Mode: [{mode}] Folder: [{self.mode_conf.mode_rec_dir}]." )

        files = findFile(self.mode_conf.mode_rec_dir, r"\.wav$", 2)

        base_cmd = [JS8_BIN, 
                "-f", str(self.mode_conf.freq_hz),
                "--js8",
                "-b", self.mode_conf.submode["code"], 
                "-d", str(DEFAULT_DECODE_DEPTH), 
                "-a", self.mode_conf.mode_rec_dir, 
                "-t", self.mode_conf.mode_tmp_dir, 
            ]

        self.logger.info(f"Processing [{len(files)}] recordings for Freq: [{freq_khz}] kHz  Submode: [{mode}]....")
        for wav_fn in files:
            src_fn = f"{self.mode_conf.mode_rec_dir}/{wav_fn}"
            decode_fn = f"{wav_fn}.decode"
            decode_ffp = f"{self.mode_conf.mode_dec_dir}/{decode_fn}"
            decode_err_ffp = f"{self.mode_conf.mode_dec_dir}/error/{decode_fn}.error"

            self.logger.debug(f"JS8 decoding process started for file: [{src_fn}].")

            cmd = base_cmd + [src_fn]

            ret_code = None
            with open(decode_ffp, "w") as decode_log, \
                open(decode_err_ffp, "w") as decode_err_log:

                # Start the process in a new session, detaching it from the current terminal
                process = subprocess.Popen(cmd,
                                        stdout=decode_log, 
                                        stderr=decode_err_log)

                ret_code = process.wait()

            if (ret_code and (ret_code != 0)):
                self.logger.error(f"Failed to decode wav file: {src_fn}  ReturnCode: [{ret_code}].") 
                os.rename(decode_ffp, f"{self.mode_conf.mode_dec_error_dir}/{decode_fn}")
            else: 
                tmp_decode_ffp = f"{self.mode_conf.mode_dec_proc_dir}/{decode_fn}"
                os.rename(decode_ffp, tmp_decode_ffp)
                os.remove(decode_err_ffp)

                # Decode using Js8Parser 
                parsedMsgs = self.js8Parser.processJs8DecodeFile(tmp_decode_ffp, None)

                if (parsedMsgs and (len(parsedMsgs) > 0)):
                    self.logger.debug(f"Decode file: [{tmp_decode_ffp}] contained [{len(parsedMsgs)}] messages.")
                    os.rename(tmp_decode_ffp, f"{self.mode_conf.mode_dec_proc_dir}/{decode_fn}")
                else:
                    # Contrain no decoded message remove it
                    os.remove(tmp_decode_ffp)

                appendJson(parsedMsgs, f"{self.mode_conf.mode_data_dir}/all_parsed_decodes.txt")

                # Handle Spots
                spots = []
                for msg in parsedMsgs:

                    self.js8FrameProc.processFrame(msg)

                    spot = generateSpot(msg)
                    if (spot is not None):
                        spots.append(f"{spot}\n")

                # Since there are many up to 40 odd freq/mode threads we need to ensure before update spots that we get lock first.
                if (len(spots) > 0):
                    lock = FileLock(f"{self.mode_conf.data_dir}/spot.lock")
                    with lock:
                        writeStringsToFile(self.mode_conf.spot_log_fn, spots, True)


            # Default to removing wav if successfully decoded and parsed. 
            # TODO: Need to possibly add option to "arvhice" / move wav file to processed / done folder
            #   os.rename(src_fn, f"{self.mode_conf.mode_rec_proc_dir}/{wav_fn}")
            os.remove(src_fn)

            self.logger.debug(f"-- JS8 decoding process completed for file: [{src_fn}].")

        self.logger.info(f"Completed processing [{len(files)}] recordings for Freq: [{freq_khz}] khz  Submode: [{mode}].")

        return 0;


    def start(self):
        self.logger.info(f"Js8Decoder handler prcessor started for Freq: [{self.mode_conf.freq_khz}] khz SubMode: [{self.mode_conf.submode['name']}]")

        while True:

            self.decoding_process()

            self.logger.info(f"Sleeping for 15secs ...")
            time.sleep(15)


################################################################################


class Js8DecodingControl:

    spot_log_fn=DEFAULT_SPOT_LOG

    freq_list = FREQ_LIST
    submodes = SUBMODES_BYNAME
    mcast_addr:str = DEFAULT_MCAST_ADDR
    data_dir:str
    archive_dir:str

    decoder_pids_file = None
    recorder_pids_file = None
    decoder_threads = []

    aprsReporter:APRSReporter

    
    def __init__(self, freq_list=FREQ_LIST, submodes=SUBMODES_BYNAME, data_dir: str=DEFAULT_DATA_DIR, mcast_addr:str=DEFAULT_MCAST_ADDR, aprsReporter:APRSReporter=None):
        self.logger = logging.getLogger("%s.%s" % (__name__, self.__class__.__name__))
        self.set_data_dir(data_dir)
        self.set_freq_list(freq_list)
        self.set_submodes(submodes)
        self.mcast_addr = mcast_addr

        self.aprsReporter = aprsReporter
        
    def set_data_dir(self, data_dir: str):
        self.data_dir = data_dir
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        self.archive_dir = f"{self.data_dir}/archive"
        Path(self.archive_dir).mkdir(parents=True, exist_ok=True)

        self.recorder_pids_file = f"{data_dir}/pcmrecord.pids"
        self.decoder_pids_file = f"{data_dir}/js8decoder.pid"


    def set_freq_list(self, freq_list):
        
        self.freq_list = []
        for freq in freq_list:
            if freq not in FREQ_LIST:
                self.logger.error(f"Invalid frequency: [{freq}] - Please select value from: [{FREQ_LIST}] kHz.")
                sys.exit(-1)

            self.freq_list.append(freq)

    def set_submodes(self, submodes):

        self.submodes = []
        for submode in submodes:
            if submode not in SUBMODES_LOOKUP:
                self.logger.error(f"Invalid submode: [{submode}] - Please select value from: [{SUBMODES_BYNAME}] kHz.")
                sys.exit(-1)

            self.submodes.append(SUBMODES_LOOKUP[submode])


    #################################################################################
    ## Decoder related functions
    #################################################################################

    def archiveDecoderPidFile(self):
        archiveFile(self.decoder_pids_file, f"{self.archive_dir}/pids")

    def loadDecoderPid(self):

        if not os.path.exists(self.decoder_pids_file):
            return {}

        # CSV Format
        #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
        with open(self.decoder_pids_file, 'r') as file:
            rec = {}
            line = file.readline()
            rd = line.strip().split(",")

            pid = None
            if (rd[0] != "None"):
                pid = int(rd[0])

            rec["pid"] = pid
            rec["timestamp"] = rd[1]

        return rec

    def checkStatusDecoder(self, pid):
        self.logger.info(f"Status for js8Decoder process PID: {pid}")

        try:
            process = psutil.Process(pid)
            self.logger.info(f"Process with PID {pid}:")
            self.logger.info(f"  Name: {process.name()}")
            self.logger.info(f"  Status: {process.status()}")
            # self.logger.info(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
            # self.logger.info(f"  Memory Info: {process.memory_info()}")
            self.logger.info(f"  Command Line: {' '.join(process.cmdline())}")

            children = process.children(recursive=True)
            for child in children:
                self.logger.info(f"  -- Child PID: {child.pid}, Name: {child.name()}, Status: {child.status()}")

        except psutil.NoSuchProcess:
            self.logger.warning(f"No process found with PID {pid}.")
        except psutil.AccessDenied:
            self.logger.error(f"Access denied to process with PID {pid}.")

        return 0;

    def checkDecoders(self):
        self.logger.info("Checking the status of the Decoding services...")
        
        rec = self.loadDecoderPid()

        if rec is not None and ('pid' not in rec):
            self.logger.warning(f"  -- No decoder processes are running. nothing to do.")
            sys.exit(0)

        pid = rec['pid']

        self.checkStatusDecoder(pid)

        return 0

    def stopDecoder(self, pid):
        self.logger.info(f"Shutting down js8decoder process PID: {pid}.")

        try:
            # Send SIGTERM (graceful termination request)
            os.kill(pid, signal.SIGTERM) 
            self.logger.info(f"  -- Sent SIGTERM to process with PID {pid}")
        except ProcessLookupError:
            self.logger.warning(f"  -- ERROR: Process with PID {pid} not found.")
        except Exception as e:
            self.logger.error(f"  -- ERROR: An error occurred: {e}")

        return 0;

    def saveDecoderPid(self):

        now_utc = datetime.now(timezone.utc)
        
        pid = os.getpid()
        timestamp = int(now_utc.timestamp())

        # CSV Format
        #   <PID>,<timestamp>
        line = f"{pid},{timestamp}\n"
        writeStringToFile(self.decoder_pids_file, line, False)

        return 0


    def stopDecoders(self):
        self.logger.info("Stopping Decoding services...")
        
        rec = self.loadDecoderPid()
        
        if rec is not None and ('pid' not in rec):
            self.logger.warning(f"  -- No decoder processes are running. nothing to do.")
            sys.exit(0)

        pid = rec['pid']

        self.stopDecoder(pid)

        self.archiveDecoderPidFile()

        return 0

    def startDecoders(self):

        
        self.logger.info("Starting Recording services...")
        
        rec = self.loadDecoderPid()

        if rec is not None and ('pid' in rec):
            self.logger.warning(f"JS8 Decoding process PID: [{rec['pid']}] already started. Please review, perform a STOP then a START again.")
            sys.exit(-1)

        # Save current PPID
        self.saveDecoderPid()

        for freq in self.freq_list:
            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                js8_dec = Js8Decoder(mode_conf, self.aprsReporter)
                dh_thread = threading.Thread(target=js8_dec.start, args=())
                dh_thread.start()
                #dh_thread.join()

        return 0
    
    #####################################################################
    ## Utility Related functions
    #####################################################################    

    def rebuildCallsignHistory(self, print_only: bool=True, aprsReporter:APRSReporter=None):
        rec = self.loadDecoderPid()

        if ((rec is not None) and ('pid' in rec) and (not print_only)):
            self.logger.warning(f"  -- Decoder processes are running. Please stop all decoders before running rebuild-callsign-history.")
            sys.exit(0)

        self.logger.info("Rebuilding callsign history log from 'all_parsed_decodes' files...")
        
        callsign_hist_db_fn = f"{self.data_dir}/callsign_history.db"
        msgbyfreq_db_fn = f"{self.data_dir}/msgfreq.db"
        msgbyfreq_incomplete_db_fn = f"{self.data_dir}/msgfreq_incomplete.db"

        # !!IMPORTANT!! 
        #    Only use aprsReporter during rebuild if debugging and issue. We DO NOT want to flood APRSIS / resend duplicates.
        # js8FrameProc = Js8FrameProcessor(aprsReporter=aprsReporter)
        js8FrameProc = Js8FrameProcessor(aprsReporter=None)

        for freq in self.freq_list:
            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                
                all_dec_fn = f"{mode_conf.mode_data_dir}/all_parsed_decodes.txt"
                self.logger.debug(f"Loading previously decoded messages from [{all_dec_fn}] for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]...")
                dec_msgs = loadJson(all_dec_fn)
                
                self.logger.debug(f"Loaded [{len(dec_msgs)}] decoded messages, rebuilding callsign history...")

                for dec in dec_msgs:
                    js8FrameProc.processFrame(dec)
                
                self.logger.info(f"Completed processing [{len(dec_msgs)}] decode messages for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}].")

        # Perform cleanup (ie move expired actvities to "msgbyfreq_db_incomplete")
        js8FrameProc.cleanup()    

        if not print_only:
            archiveFile(callsign_hist_db_fn, f"{self.archive_dir}/callsign_hist_db", ARCHIVE_METHOD_TRUNCATE)
            archiveFile(msgbyfreq_db_fn, f"{self.archive_dir}/callsign_hist_db", ARCHIVE_METHOD_TRUNCATE)
            archiveFile(msgbyfreq_incomplete_db_fn, f"{self.archive_dir}/callsign_hist_db", ARCHIVE_METHOD_TRUNCATE)
            appendJson(js8FrameProc.callsigns, callsign_hist_db_fn)
            appendJson(js8FrameProc.msgByFreq, msgbyfreq_db_fn)
            appendJson(js8FrameProc.msgByFreq_incomplete, msgbyfreq_incomplete_db_fn)
        else:
            dbs = {
                "callsign_hist_db": js8FrameProc.callsigns,
                "msgbyfreq_db": js8FrameProc.msgByFreq,
                "msgbyfreq_db_incomplete": js8FrameProc.msgByFreq_incomplete
            }
            print(f"{json.dumps(dbs)}")
            

        self.logger.info(f"Completed rebuilding callsign history DB: [{callsign_hist_db_fn}]")

        return 0


    def rebuildSpots(self, print_only: bool=True):

        rec = self.loadDecoderPid()

        if ((rec is not None) and ('pid' in rec) and (not print_only)):
            self.logger.warning(f"  -- Decoder processes are running. Please stop all decoders before running rebuild-spots.")
            sys.exit(0)

        self.logger.info("Rebuilding spot log from 'all_parsed_decodes' files...")
        
        spots = []

        for freq in self.freq_list:
            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                js8_dec = Js8Decoder(mode_conf)
                
                all_dec_fn = f"{mode_conf.mode_data_dir}/all_parsed_decodes.txt"
                self.logger.debug(f"Loading previously decoded messages from [{all_dec_fn}] for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]...")
                dec_msgs = loadJson(all_dec_fn)
                
                self.logger.debug(f"Loaded [{len(dec_msgs)}] decoded messages, rebuilding spots...")

                for dec in dec_msgs:
                    spot = generateSpot(dec)
                    if (spot):
                        spots.append(f"{spot}\n")
                        
                self.logger.info(f"Completed processing [{len(dec_msgs)}] decode messages for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]. Reported [{len(spots)}] new spots.")

        # arrange by timestamp, freq, .....
        spots.sort()
        
        if not print_only:
            archiveFile(self.spot_log_fn, f"{self.archive_dir}/spots", ARCHIVE_METHOD_TRUNCATE)
            writeStringsToFile(self.spot_log_fn, spots, False)
        else:
            # Moved here so printing out the sorted result
            for spot in spots:
                print (spot, end="")

        self.logger.info(f"Completed rebuilding spot log, located [{len(spots)}] spots.")

        return 0

    def rebuildAllDecodes(self, print_only: bool=True):

        rec = self.loadDecoderPid()

        if ((rec is not None) and ('pid' in rec) and (not print_only)):
            self.logger.warning(f"  -- Decoder processes are running. Please stop all decoders before running rebuild-alldecodes.")
            sys.exit(0)


        self.logger.info("Rebuilding 'all_parsed_decodes' by reparsing all archived JS8 decode files...")
        
        for freq in self.freq_list:
            js8_parser = Js8Parser(freq, "usb")

            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                
                dec_files = findFile(mode_conf.mode_dec_proc_dir, r"\.decode$", 2, True)

                dec_msgs = []

                for dec_fn in dec_files:
                    decode_ffp = f"{mode_conf.mode_dec_proc_dir}/{dec_fn}"
                    parsedMsgs = js8_parser.processJs8DecodeFile(decode_ffp, None)

                    if (parsedMsgs and (len(parsedMsgs) > 0)):

                        for msg in parsedMsgs:
                            dec_msgs.append(msg)
                            if print_only:
                                print(json.dumps(msg))
                            

                self.logger.info(f"Completed processing [{len(dec_msgs)}] decode messages for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]...")

        
                if not print_only:
                    all_dec_fn = f"{mode_conf.mode_data_dir}/all_parsed_decodes.txt";
        
                    archiveFile(all_dec_fn, f"{self.archive_dir}/alldecodes")
                    appendJson(dec_msgs, all_dec_fn)            
    

        return 0

        
    #####################################################################
    ## Recording Related functions
    #####################################################################    

    def stopRecorder(self, rec):
        self.logger.info(f"Shutting down pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

        pid = rec["pid"]

        try:
            # Send SIGTERM (graceful termination request)
            os.kill(rec['pid'], signal.SIGTERM) 
            self.logger.info(f"  -- Sent SIGTERM to process with PID {pid}")
        except ProcessLookupError:
            self.logger.warning(f"Process with PID {pid} not found.")
        except Exception as e:
            self.logger.error(f"An error occurred: {e}")

        return 0;

    def checkStatusRecorder(self, rec):
        self.logger.info(f"Status for pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

        pid = rec["pid"]

        try:
            process = psutil.Process(pid)
            self.logger.info(f"Process with PID {pid}:")
            self.logger.info(f"  Name: {process.name()}")
            self.logger.info(f"  Status: {process.status()}")
            # self.logger.info(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
            # self.logger.info(f"  Memory Info: {process.memory_info()}")
            self.logger.info(f"  Command Line: {' '.join(process.cmdline())}")
        except psutil.NoSuchProcess:
            self.logger.warning(f"No process found with PID {pid}.")
        except psutil.AccessDenied:
            self.logger.error(f"Access denied to process with PID {pid}.")

        return 0;

    def loadRecordPids(self):

        if not os.path.exists(self.recorder_pids_file):
            return []

        # CSV Format
        #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
        with open(self.recorder_pids_file, 'r') as file:
            recs = []
            for line in file:
                rd = line.strip().split(",")

                pid = None
                if (rd[5] != "None"):
                    pid = int(rd[5])

                ret_code = None
                if (rd[7] != "None"):
                    ret_code = int(rd[7])

                rec = {
                    "freq_khz": int(rd[0]),
                    "freq_hz": int(rd[1]),
                    "submode": rd[2],
                    "submode_duration": int(rd[3]),
                    "mcast_addr": rd[4],
                    "pid": pid,
                    "timestamp": rd[6],
                    "ret_code": ret_code
                }

                recs.append(rec)

        self.logger.info(f"  - Loaded {len(recs)} records from recorder pids file: [{self.recorder_pids_file}].")

        return recs

    def saveRecordPids(self, recs):

        # CSV Format
        #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
        
        pid_recs = []
        for rec in recs:
            
            pid_rec = f"{rec['freq_khz']}," + \
                f"{rec['freq_hz']}," + \
                f"{rec['submode']}," + \
                f"{rec['submode_duration']}," + \
                f"{rec['mcast_addr']}," + \
                f"{rec['pid']}," + \
                f"{rec['timestamp']}," + \
                f"{rec['ret_code']}\n"

            pid_recs.append(pid_rec);

        writeStringsToFile(self.recorder_pids_file, pid_recs, False)

        self.logger.info(f"Saved {len(recs)} records to recorder pids file: [{self.recorder_pids_file}].")

        return recs


    def startRecorders(self):
        self.logger.info("Starting Recording services...")
        
        recs = self.loadRecordPids()
        recs_cnt = len(recs)
        if (recs_cnt > 0):
            self.logger.warning(f"There are {recs_cnt} PIDs already started. Please review, perform a STOP then a START again.")
            sys.exit(-1)

        recs = []

        for freq in self.freq_list:
            for submode in self.submodes:
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                js8_rec = Js8Recorder(mode_conf)
                rec = js8_rec.start()
                recs.append(rec)

        self.saveRecordPids(recs)

        return 0

    def archiveRecorderPidsFile(self):
        archiveFile(self.recorder_pids_file, f"{self.archive_dir}/pids")
        

    def stopRecorders(self):
        self.logger.info("Stopping Recording services...")
        
        recs = self.loadRecordPids()

        recs_cnt = len(recs)
        if (recs_cnt == 0):
            self.logger.warning(f"There are 0 records running. nothing to do.")
            sys.exit(0)

        for rec in recs:
            self.stopRecorder(rec)

        self.archiveRecorderPidsFile()

        return 0

    def checkRecorders(self):
        self.logger.info("Checking the status of the Recording services...")
        
        recs = self.loadRecordPids()

        recs_cnt = len(recs)
        if (recs_cnt == 0):
            self.logger.warning(f"There are 0 records running. nothing to do.")
            sys.exit(0)


        for rec in recs:
            self.checkStatusRecorder(rec)

        return 0


#################################################################################
## Helper / Utils functions
#################################################################################

def generateSpot(dec):
        if (dec["spot"] and dec["is_valid"]):
            return f"{dec['record_time']} {dec['db']:>5} {dec['dt']:>4} {dec['js8mode']} {dec['freq']/1000000:>9} {dec['callsign']:>9} {dec['locator']:>4} ~ {dec['msg']}"

        return None

def processArgs(parser):

    parser = argparse.ArgumentParser(description="KA9Q-Radio Js8 Decoding Controler.")
    parser.add_argument("process", type=str, choices=['record','decode', 'rebuild-spots', 'rebuild-alldecodes', 'rebuild-history'], help="The process to execute (e.g., 'record', 'decode')")
    parser.add_argument("-a", "--action", type=str, choices=['start', 'stop', 'status'], default="status", help="The action to execute (e.g., 'start', 'stop', 'status')")

    # Used by Processes (rebuild-spots, rebuild-alldecodes) allowing to print data only and not update. 
    #   Note: Decoders need to be stopped otherwise to allow updating of spots/alldecode files.
    parser.add_argument("-po", "--print-only", action="store_true", help="The action to execute (e.g., 'start', 'stop', 'status')")

    parser.add_argument("-f", "--freq", type=int, nargs='+', default=FREQ_LIST, help="Limit recording processes to 1 or more frequencies. Frquency is that of the radio dial frequency in Hz. If ommited then all standard js8call frequencies will be used.")
    parser.add_argument("-m", "--mode", type=str, default="usb", help="Radio Mode (usb / lsb).")
    parser.add_argument("-sm", "--sub-mode", type=str, nargs='+', default=SUBMODES_BYNAME,  help="Limit the recording process per frequency to a specific set of 1 or more JS7 'sub-modes' (slow, norm, fast, turbo).")
    parser.add_argument("-d", "--data-dir", type=str, default=DEFAULT_DATA_DIR, help="Data directory for storing (recordings, decodes, logs etc).")
    parser.add_argument("-ma", "--mcast-addr", type=str, default=DEFAULT_MCAST_ADDR, help="Enable verbose output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--aprsis", action="store_true", help="Enables processing received APRSIS commands (ie position reporting)")
    parser.add_argument("--aprs-host", type=str, default=DEFAULT_APRS_HOST, help="APRSIS Host name / IP")
    parser.add_argument("--aprs-port", type=int, default=DEFAULT_APRS_PORT, help="APRSIS Port")
    parser.add_argument("--aprs-user", type=str, help="Enables processing APRSIS commands (ie position reporting)")
    parser.add_argument("--aprs-passcode", type=str, help="APRSIS password (see https://apps.magicbug.co.uk/passcode/)")
    parser.add_argument("--aprs-reporter", type=str, help="Callsign to be used as the reporter.")
    
    args = parser.parse_args()

    return args

def initAprsReporter(args):
    
    if (args.aprsis):

        if isEmpty(args.aprs_reporter):
            logError("APRIS processing enabled - APRS Reporter is required.", -1)

        if isEmpty(args.aprs_user):
            logError("APRIS processing enabled - APRS User is required.", -1)

        if isEmpty(args.aprs_passcode):
            logError("APRIS processing enabled - APRS Passcode is required.", -1)            

        log_fn = f"{args.data_dir}/aprsis_frames.log"
        return APRSReporter(reporter=args.aprs_reporter, 
                            user=args.aprs_user, passcode=args.aprs_passcode, 
                            host=args.aprs_host, port=args.aprs_port, log_fn=log_fn)

    return None

########
## Main
########

def main():
    
    parser = argparse.ArgumentParser(description="KA9Q-Radio Js8 Decoding Controler.")
    args = processArgs(parser)
        
    aprsReporter = initAprsReporter(args)
    js8_dc = Js8DecodingControl(args.freq, args.sub_mode, args.data_dir, args.mcast_addr, aprsReporter=aprsReporter)

    glogger.info(f"Performing Process: [{args.process}] Action: [{args.action}]")

    if (args.process == "record"):
        if args.action == "start":
            js8_dc.startRecorders()
        elif args.action == "stop":
            js8_dc.stopRecorders()
        elif args.action == "status":
            js8_dc.checkRecorders()
        else:
            glogger.error(f"Unknown recording action: {args.command}")
            parser.print_help()

    elif (args.process == "decode"):
        if args.action == "start":
            js8_dc.startDecoders()
        elif args.action == "stop":
            js8_dc.stopDecoders()
        elif args.action == "status":
            js8_dc.checkDecoders()
        else:
            glogger.error(f"Unknown recording action: {args.command}")
            parser.print_help()

    elif (args.process == "rebuild-spots"):
        js8_dc.rebuildSpots(args.print_only);

    elif (args.process == "rebuild-alldecodes"):
        js8_dc.rebuildAllDecodes(args.print_only);
    
    elif (args.process == "rebuild-history"):
        js8_dc.rebuildCallsignHistory(args.print_only, aprsReporter=aprsReporter);

    else:
        glogger.error(f"Unknown process: {args.command} requested.")
        parser.print_help()


if __name__ == "__main__":
        main()