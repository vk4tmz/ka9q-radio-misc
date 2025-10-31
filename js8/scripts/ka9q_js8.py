#!/usr/bin/python3

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
import shutil
import signal
import subprocess
import sys
import threading
import time

from datetime import datetime, timezone
from pathlib import Path
from ka9q_js8Parser import Js8Parser

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
FREQ_SSRC=[1842, 3578, 7078, 10130, 14078, 18105, 21078, 24922, 28078, 27246]

data_dir = DEFAULT_DATA_DIR

DEFAULT_DECODE_DEPTH = 3

ARCHIVE_METHOD_MOVE="AMM"
ARCHIVE_METHOD_TRUNCATE="AMT"


# Configure basic logging to a file and the console
logging.basicConfig(
    level=logging.INFO,  # Set the minimum logging level to INFO
#    level=logging.DEBUG,  # Set the minimum logging level to INFO    
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler("ka9q-j8.log"),  # Log to a file
        logging.StreamHandler()  # Log to the console (standard output)
    ]
)

logger = logging.getLogger(__name__)


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

        logger.info(f"Starting new pcmrecord process for Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}]")

        # Determine SSRC
        freq_idx = FREQ_LIST.index(freq_khz)
        freq_ssrc = FREQ_SSRC[freq_idx]
        logger.info(f"Selected SSRC: [{freq_ssrc}] for Freq: [{freq_khz}]")

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
            logger.error(f"Command failed with return code: {process.returncode}")

        return rec;


#################################################################################
# Js8Decoder Class
#################################################################################

class Js8Decoder:
    mode_conf: ModeConfig = None
    js8Parser: Js8Parser = None

    def __init__(self, mode_conf: ModeConfig):
        self.mode_conf = mode_conf
        self.js8Parser = Js8Parser(self.mode_conf.freq_khz, "usb")

    def decoding_process(self):

        now_utc = datetime.now(timezone.utc)

        freq_khz = self.mode_conf.freq_khz
        mode = self.mode_conf.submode['name']

        logger.info(f"Starting js8Decoder process for Freq: [{freq_khz}] kHz Mode: [{mode}] Folder: [{self.mode_conf.mode_rec_dir}]." )

        files = findFile(self.mode_conf.mode_rec_dir, r"\.wav$", 2)

        base_cmd = [JS8_BIN, 
                "-f", str(self.mode_conf.freq_hz),
                "--js8",
                "-b", self.mode_conf.submode["code"], 
                "-d", str(DEFAULT_DECODE_DEPTH), 
                "-a", self.mode_conf.mode_rec_dir, 
                "-t", self.mode_conf.mode_tmp_dir, 
            ]

        logger.info(f"Processing [{len(files)}] recordings for Freq: [{freq_khz}] kHz  Submode: [{mode}]....")
        for wav_fn in files:
            src_fn = f"{self.mode_conf.mode_rec_dir}/{wav_fn}"
            decode_fn = f"{wav_fn}.decode"
            decode_ffp = f"{self.mode_conf.mode_dec_dir}/{decode_fn}"
            decode_err_ffp = f"{self.mode_conf.mode_dec_dir}/error/{decode_fn}.error"

            logger.debug(f"JS8 decoding process started for file: [{src_fn}].")

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
                logger.error(f"Failed to decode wav file: {src_fn}  ReturnCode: [{ret_code}].") 
                os.rename(decode_ffp, f"{self.mode_conf.mode_dec_error_dir}/{decode_fn}")
            else: 
                tmp_decode_ffp = f"{self.mode_conf.mode_dec_proc_dir}/{decode_fn}"
                os.rename(decode_ffp, tmp_decode_ffp)
                os.remove(decode_err_ffp)

                # Decode using Js8Parser 
                parsedMsgs = self.js8Parser.processJs8DecodeFile(tmp_decode_ffp, None)

                if (parsedMsgs and (len(parsedMsgs) > 0)):
                    logger.debug(f"Decode file: [{tmp_decode_ffp}] contained [{len(parsedMsgs)}] messages.")
                    os.rename(tmp_decode_ffp, f"{self.mode_conf.mode_dec_proc_dir}/{decode_fn}")
                else:
                    # Contrain no decoded message remove it
                    os.remove(tmp_decode_ffp)

                appendJson(parsedMsgs, f"{self.mode_conf.mode_data_dir}/all_parsed_decodes.txt")

                # Handle Spots
                spots = []
                for msg in parsedMsgs:
                    spots.append(f"{generateSpot(msg)}\n")

                # Since there are many up to 40 odd freq/mode threads we need to ensure before update spots that we get lock first.
                if (len(spots) > 0):
                    lock = FileLock(f"{self.mode_conf.data_dir}/spot.lock")
                    with lock:
                        writeStringsToFile(self.mode_conf.spot_log_fn, spots, True)


            # Move wav file to processed / done folder
            os.rename(src_fn, f"{self.mode_conf.mode_rec_proc_dir}/{wav_fn}")

            logger.debug(f"-- JS8 decoding process completed for file: [{src_fn}].")

        logger.info(f"Completed processing [{len(files)}] recordings for Freq: [{freq_khz}] khz  Submode: [{mode}].")

        return 0;


    def start(self):
        logger.info(f"Js8Decoder handler prcessor started for Freq: [{self.mode_conf.freq_khz}] khz SubMode: [{self.mode_conf.submode['name']}]")

        while True:

            self.decoding_process()

            logger.info(f"Sleeping for 15secs ...")
            time.sleep(15)       


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

    
    def __init__(self, freq_list=FREQ_LIST, submodes=SUBMODES_BYNAME, data_dir: str=DEFAULT_DATA_DIR, mcast_addr:str=DEFAULT_MCAST_ADDR):
        self.set_data_dir(data_dir)
        self.set_freq_list(freq_list)
        self.set_submodes(submodes)
        self.mcast_addr = mcast_addr
        
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
                logger.error(f"Invalid frequency: [{freq}] - Please select value from: [{FREQ_LIST}] kHz.")
                sys.exit(-1)

            self.freq_list.append(freq)

    def set_submodes(self, submodes):

        self.submodes = []
        for submode in submodes:
            if submode not in SUBMODES_LOOKUP:
                logger.error(f"Invalid submode: [{submode}] - Please select value from: [{SUBMODES_BYNAME}] kHz.")
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
        logger.info(f"Status for js8Decoder process PID: {pid}")

        try:
            process = psutil.Process(pid)
            logger.info(f"Process with PID {pid}:")
            logger.info(f"  Name: {process.name()}")
            logger.info(f"  Status: {process.status()}")
            # logger.info(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
            # logger.info(f"  Memory Info: {process.memory_info()}")
            logger.info(f"  Command Line: {' '.join(process.cmdline())}")

            children = process.children(recursive=True)
            for child in children:
                logger.info(f"  -- Child PID: {child.pid}, Name: {child.name()}, Status: {child.status()}")

        except psutil.NoSuchProcess:
            logger.warning(f"No process found with PID {pid}.")
        except psutil.AccessDenied:
            logger.error(f"Access denied to process with PID {pid}.")

        return 0;

    def checkDecoders(self):
        logger.info("Checking the status of the Decoding services...")
        
        rec = self.loadDecoderPid()

        if rec is not None and ('pid' not in rec):
            logger.warning(f"  -- No decoder processes are running. nothing to do.")
            sys.exit(0)

        pid = rec['pid']

        self.checkStatusDecoder(pid)

        return 0

    def stopDecoder(self, pid):
        logger.info(f"Shutting down js8decoder process PID: {pid}.")

        try:
            # Send SIGTERM (graceful termination request)
            os.kill(pid, signal.SIGTERM) 
            logger.info(f"  -- Sent SIGTERM to process with PID {pid}")
        except ProcessLookupError:
            logger.warning(f"  -- ERROR: Process with PID {pid} not found.")
        except Exception as e:
            logger.error(f"  -- ERROR: An error occurred: {e}")

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
        logger.info("Stopping Decoding services...")
        
        rec = self.loadDecoderPid()
        
        if rec is not None and ('pid' not in rec):
            logger.warning(f"  -- No decoder processes are running. nothing to do.")
            sys.exit(0)

        pid = rec['pid']

        self.stopDecoder(pid)

        self.archiveDecoderPidFile()

        return 0

    def startDecoders(self):

        
        logger.info("Starting Recording services...")
        
        rec = self.loadDecoderPid()

        if rec is not None and ('pid' in rec):
            logger.warning(f"JS8 Decoding process PID: [{rec['pid']}] already started. Please review, perform a STOP then a START again.")
            sys.exit(-1)

        # Save current PPID
        self.saveDecoderPid()

        for freq in self.freq_list:
            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                js8_dec = Js8Decoder(mode_conf)
                dh_thread = threading.Thread(target=js8_dec.start, args=())
                dh_thread.start()
                #dh_thread.join()

        return 0
    
    #####################################################################
    ## Utility Related functions
    #####################################################################    


    def rebuildSpots(self, print_only: bool=True):

        rec = self.loadDecoderPid()

        if ((rec is not None) and ('pid' in rec) and (not print_only)):
            logger.warning(f"  -- Decoder processes are running. Please stop all decoders before running rebuild-spots.")
            sys.exit(0)

        logger.info("Rebuilding spot log from 'all_parsed_decodes' files...")
        
        spots = []

        for freq in self.freq_list:
            for submode in self.submodes:
                
                # Create a Decoder Hanlding thread.
                #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
                mode_conf = ModeConfig(freq, submode, self.data_dir, self.mcast_addr)
                js8_dec = Js8Decoder(mode_conf)
                
                all_dec_fn = f"{mode_conf.mode_data_dir}/all_parsed_decodes.txt"
                logger.debug(f"Loading previously decoded messages from [{all_dec_fn}] for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]...")
                dec_msgs = loadJson(all_dec_fn)
                
                logger.debug(f"Loaded [{len(dec_msgs)}] decoded messages, rebuilding spots...")

                for dec in dec_msgs:
                    spot = generateSpot(dec)
                    if (spot):
                        spots.append(f"{spot}\n")
                        
                logger.info(f"Completed processing [{len(dec_msgs)}] decode messages for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]. Reported [{len(spots)}] new spots.")

        # arrange by timestamp, freq, .....
        spots.sort()
        
        if not print_only:
            archiveFile(self.spot_log_fn, f"{self.archive_dir}/spots", ARCHIVE_METHOD_TRUNCATE)
            writeStringsToFile(self.spot_log_fn, spots, False)
        else:
            # Moved here so printing out the sorted result
            for spot in spots:
                print (spot, end="")

        logger.info(f"Completed rebuilding spot log, located [{len(spots)}] spots.")

        return 0

    def rebuildAllDecodes(self, print_only: bool=True):

        rec = self.loadDecoderPid()

        if ((rec is not None) and ('pid' in rec) and (not print_only)):
            logger.warning(f"  -- Decoder processes are running. Please stop all decoders before running rebuild-alldecodes.")
            sys.exit(0)


        logger.info("Rebuilding 'all_parsed_decodes' by reparsing all archived JS8 decode files...")
        
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
                            

                logger.info(f"Completed processing [{len(dec_msgs)}] decode messages for Freq: [{mode_conf.freq_khz}] kHz  Submode: [{mode_conf.submode['name']}]...")

        
                if not print_only:
                    all_dec_fn = f"{mode_conf.mode_data_dir}/all_parsed_decodes.txt";
        
                    archiveFile(all_dec_fn, f"{self.archive_dir}/alldecodes")
                    appendJson(dec_msgs, all_dec_fn)            
    

        return 0

        
    #####################################################################
    ## Recording Related functions
    #####################################################################    

    def stopRecorder(self, rec):
        logger.info(f"Shutting down pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

        pid = rec["pid"]

        try:
            # Send SIGTERM (graceful termination request)
            os.kill(rec['pid'], signal.SIGTERM) 
            logger.info(f"  -- Sent SIGTERM to process with PID {pid}")
        except ProcessLookupError:
            logger.warning(f"Process with PID {pid} not found.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")

        return 0;

    def checkStatusRecorder(self, rec):
        logger.info(f"Status for pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

        pid = rec["pid"]

        try:
            process = psutil.Process(pid)
            logger.info(f"Process with PID {pid}:")
            logger.info(f"  Name: {process.name()}")
            logger.info(f"  Status: {process.status()}")
            # logger.info(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
            # logger.info(f"  Memory Info: {process.memory_info()}")
            logger.info(f"  Command Line: {' '.join(process.cmdline())}")
        except psutil.NoSuchProcess:
            logger.warning(f"No process found with PID {pid}.")
        except psutil.AccessDenied:
            logger.error(f"Access denied to process with PID {pid}.")

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

        logger.info(f"  - Loaded {len(recs)} records from recorder pids file: [{self.recorder_pids_file}].")

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

        logger.info(f"Saved {len(recs)} records to recorder pids file: [{self.recorder_pids_file}].")

        return recs


    def startRecorders(self):
        logger.info("Starting Recording services...")
        
        recs = self.loadRecordPids()
        recs_cnt = len(recs)
        if (recs_cnt > 0):
            logger.warning(f"There are {recs_cnt} PIDs already started. Please review, perform a STOP then a START again.")
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
        logger.info("Stopping Recording services...")
        
        recs = self.loadRecordPids()

        recs_cnt = len(recs)
        if (recs_cnt == 0):
            logger.warning(f"There are 0 records running. nothing to do.")
            sys.exit(0)

        for rec in recs:
            self.stopRecorder(rec)

        self.archiveRecorderPidsFile()

        return 0

    def checkRecorders(self):
        logger.info("Checking the status of the Recording services...")
        
        recs = self.loadRecordPids()

        recs_cnt = len(recs)
        if (recs_cnt == 0):
            logger.warning(f"There are 0 records running. nothing to do.")
            sys.exit(0)


        for rec in recs:
            self.checkStatusRecorder(rec)

        return 0


#################################################################################
## Helper / Utils functions
#################################################################################

def truncateFile(fn):
    with open(fn, 'w') as f:
        pass 

def archiveFile(fn:str, archiveDir: str=None, archiveMethod: str=ARCHIVE_METHOD_MOVE):

    try:
        now = datetime.now()
        dt_suffix = datetime.now().strftime("%Y%m%d_%H%M%S.%f")[:-3]

        if os.path.exists(fn):
            
            fn_path, fn_base = os.path.split(fn)
            tmp_fn = f"{fn_base}.{dt_suffix}"
            
            if ((archiveDir is not None) and (archiveDir != '')):
                Path(archiveDir).mkdir(parents=True, exist_ok=True)
                src=fn
                dest=f"{archiveDir}/{tmp_fn}"
            else:
                src=fn
                dest=f"{fn_path}/{tmp_fn}"

            if (archiveMethod == ARCHIVE_METHOD_TRUNCATE):
                # To preserve file perms we COPY original to destination then trucate the existing file.
                shutil.copy(src, dest)
                truncateFile(fn)
            else:
                # Move original and allow process to create new file
                shutil.move(src, dest)

    except Exception as e:
        logger.error(f"Failed to archive file: [{fn}] to folder: [{archiveDir}] {e}")


def writeStringsToFile(out_fn: str, str_list: list, append: bool=True):
    wmode = "w"
    if (append == True):
        wmode ="a"

    with open(out_fn, wmode) as file:                    
            for item in str_list:
                if (item is not None):
                    file.write(item)

    return 0

def writeStringToFile(out_fn: str, item: str, append: bool=True):
    wmode = "w"
    if append:
        wmode ="a"

    with open(out_fn, wmode) as file:                    
            file.write(item)

    return 0

def appendJson(parsedMsgs, log_fn):
    with open(log_fn, 'a') as file:

        for msg in parsedMsgs:
            file.write(f"{json.dumps(msg)}\n")

def loadJson(log_fn):
    msgs = []
    with open(log_fn, 'r') as file:

        for line in file:
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"Invalid decode message: [{line}] ignored.")

    return msgs

def generateSpot(dec):
        if (dec["spot"]):    
            return f"{dec['record_time']} {dec['db']:>5} {dec['dt']:>4} {dec['js8mode']} {dec['freq']/1000000:>9} {dec['callsign']:>9} {dec['locator']:>4} ~ {dec['msg']}"

        return None


def findFile(dirss, re_pat, age_secs, sort:bool=True):
    curr_time = time.time();

    files = []

    with os.scandir(dirss) as listOfEntries:
        for entry in listOfEntries:
            # If regex pattern provide only include file if it matches
            if (re_pat):
                res = re.search(re_pat, entry.name)
                if not res:
                    continue;
            
            age = curr_time - entry.stat().st_mtime
            if age > age_secs:
                files.append(entry.name)

    if sort:
        files.sort()

    return files

def processArgs(parser):

    parser = argparse.ArgumentParser(description="KA9Q-Radio Js8 Decoding Controler.")
    parser.add_argument("process", type=str, choices=['record','decode', 'rebuild-spots', 'rebuild-alldecodes'], help="The process to execute (e.g., 'record', 'decode')")
    parser.add_argument("-a", "--action", type=str, choices=['start', 'stop', 'status'], default="status", help="The action to execute (e.g., 'start', 'stop', 'status')")

    # Used by Processes (rebuild-spots, rebuild-alldecodes) allowing to print data only and not update. 
    #   Note: Decoders need to be stopped otherwise to allow updating of spots/alldecode files.
    parser.add_argument("-po", "--print-only", action="store_true", help="The action to execute (e.g., 'start', 'stop', 'status')")

    parser.add_argument("-f", "--freq", type=int, nargs='+', default=FREQ_LIST, help="Limit recording processes to 1 or more frequencies. Frquency is that of the radio dial frequency in Hz. If ommited then all standard js8call frequencies will be used.")
    parser.add_argument("-m", "--mode", type=str, default="usb", help="Radio Mode (usb / lsb).")
    parser.add_argument("-sm", "--sub_mode", type=str, nargs='+', default=SUBMODES_BYNAME,  help="Limit the recording process per frequency to a specific set of 1 or more JS7 'sub-modes' (slow, norm, fast, turbo).")
    parser.add_argument("-d", "--data-dir", type=str, default=DEFAULT_DATA_DIR, help="Data directory for storing (recordings, decodes, logs etc).")
    parser.add_argument("-ma", "--mcast-addr", type=str, default=DEFAULT_MCAST_ADDR, help="Enable verbose output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    
    args = parser.parse_args()

    return args

########
## Main
########

def main():
    
    parser = argparse.ArgumentParser(description="KA9Q-Radio Js8 Decoding Controler.")
    args = processArgs(parser)
    
    js8_dc = Js8DecodingControl(args.freq, args.sub_mode, args.data_dir, args.mcast_addr)

    logger.info(f"Performing Process: [{args.process}] Action: [{args.action}]")

    if (args.process == "record"):
        if args.action == "start":
            js8_dc.startRecorders()
        elif args.action == "stop":
            js8_dc.stopRecorders()
        elif args.action == "status":
            js8_dc.checkRecorders()
        else:
            logger.error(f"Unknown recording action: {args.command}")
            parser.print_help()

    elif (args.process == "decode"):
        if args.action == "start":
            js8_dc.startDecoders()
        elif args.action == "stop":
            js8_dc.stopDecoders()
        elif args.action == "status":
            js8_dc.checkDecoders()
        else:
            logger.error(f"Unknown recording action: {args.command}")
            parser.print_help()

    elif (args.process == "rebuild-spots"):
        js8_dc.rebuildSpots(args.print_only);

    elif (args.process == "rebuild-alldecodes"):
        js8_dc.rebuildAllDecodes(args.print_only);
    

    else:
        logger.error(f"Unknown process: {args.command} requested.")
        parser.print_help()


if __name__ == "__main__":
        main()