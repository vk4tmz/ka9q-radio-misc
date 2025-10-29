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
import psutil
import os
import signal
import re
import subprocess
import sys
import threading
import time

from datetime import datetime, timezone
from pathlib import Path
from ka9q_js8Parser import Js8Parser

DEFAULT_DATA_DIR="./data"
DEFAULT_MCAST_ADDR="js8-pcm.local"

PCMRECORD_BIN = "/usr/local/bin/pcmrecord"
JS8_BIN="/usr/bin/js8"

SM_TURBO = {'name': "turbo", 'code': "C", "duration": 6}
SM_FAST  = {'name': "fast", 'code': "B", "duration": 10}
SM_NORM  = {'name': "norm", 'code': "A", "duration": 15} 
SM_SLOW  = {'name': "slow", 'code': "E", "duration": 30}

DEFAULT_SUBMODES_BYNAME = [SM_TURBO["name"], SM_FAST["name"], SM_NORM["name"], SM_SLOW["name"]]

SUBMODES = [SM_TURBO, SM_FAST, SM_NORM, SM_SLOW]

SUBMODES_LOOKUP = {
    "turbo": SM_TURBO, 
    "fast": SM_FAST, 
    "norm": SM_NORM, 
    "slow": SM_SLOW,
}

FREQ_LIST=[1842, 3578, 7078, 10130, 14078, 18104, 21078, 24922, 28078, 27246]
# SSRC autogen can/will eventually vary from actualy freq_khz (ie 17m clashes with FT8/FT4)
FREQ_SSRC=[1842, 3578, 7078, 10130, 14078, 18106, 21078, 24922, 28078, 27246]

data_dir = DEFAULT_DATA_DIR
recorder_pids_file = f"{data_dir}/pcmrecord.pids"
decoder_pids_file = f"{data_dir}/js8decoder.pid"
freq_list = []
submodes = []

DEFAULT_DECODE_DEPTH = 3

decoder_threads = []

mode_root_dir=DEFAULT_DATA_DIR
mode_rec_dir=DEFAULT_DATA_DIR
mode_rec_error_dir=DEFAULT_DATA_DIR
mode_rec_proc_dir=DEFAULT_DATA_DIR
mode_data_dir=DEFAULT_DATA_DIR
mode_dec_dir=DEFAULT_DATA_DIR
mode_dec_error_dir=DEFAULT_DATA_DIR
mode_dec_proc_dir=DEFAULT_DATA_DIR
mode_tmp_dir=DEFAULT_DATA_DIR

# TODO: Remove this once converted to class as only needs to be local
args = None

parser = argparse.ArgumentParser(description="A simple script with arguments.")

def processArgs():


    parser.add_argument("process", help="The process to execute (e.g., 'record', 'decode')")
    parser.add_argument("action", help="The action to execute (e.g., 'start', 'stop', 'status')")

    parser.add_argument("-f", "--freq", type=int, nargs='+', default=FREQ_LIST, help="Limit recording processes to 1 or more frequencies. Frquency is that of the radio dial frequency in Hz. If ommited then all standard js8call frequencies will be used.")
    parser.add_argument("-m", "--mode", type=str, default="usb", help="Radio Mode (usb / lsb).")
    parser.add_argument("-sm", "--sub_mode", type=str, nargs='+', default=DEFAULT_SUBMODES_BYNAME,  help="Limit the recording process per frequency to a specific set of 1 or more JS7 'sub-modes' (slow, norm, fast, turbo).")
    parser.add_argument("-d", "--data-dir", type=str, default=DEFAULT_DATA_DIR, help="Data directory for storing (recordings, decodes, logs etc).")
    parser.add_argument("-ma", "--mcast-addr", type=str, default=DEFAULT_MCAST_ADDR, help="Enable verbose output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    
    args = parser.parse_args()

    data_dir = args.data_dir
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    recorder_pids_file = f"{data_dir}/pcmrecord.pids"   

    for freq in args.freq:
        if freq not in FREQ_LIST:
            print(f"ERROR: Invalid frequency: [{freq}] - Please select value from: [{FREQ_LIST}] kHz.")
            sys.exit(-1)

        freq_list.append(freq)

    for submode in args.sub_mode:
        if submode not in SUBMODES_LOOKUP:
            print(f"ERROR: Invalid submode: [{submode}] - Please select value from: [{SUBMODES_LOOKUP}] kHz.")
            sys.exit(-1)

        submodes.append(SUBMODES_LOOKUP[submode])

    return args

def setupSubmodeFolders(freq_khz, mode):
    # Setup mode specific folders
    
    global mode_root_dir
    global mode_rec_dir
    global mode_rec_error_dir
    global mode_rec_proc_dir
    global mode_data_dir
    global mode_dec_dir
    global mode_dec_error_dir
    global mode_dec_proc_dir
    global mode_tmp_dir

    freq_hz = freq_khz * 1000

    mode_root_dir = f"{data_dir}/{freq_hz}/{mode}" 
    mode_rec_dir = f"{mode_root_dir}/rec"
    mode_rec_error_dir = f"{mode_rec_dir}/error"
    mode_rec_proc_dir = f"{mode_rec_dir}/done"
    mode_data_dir = f"{mode_root_dir}/data"
    mode_dec_dir = f"{mode_data_dir}/decode"
    mode_dec_error_dir = f"{mode_dec_dir}/error"
    mode_dec_proc_dir = f"{mode_dec_dir}/done"
    mode_tmp_dir = f"{data_dir}/{freq_hz}/{mode}/tmp"

    Path(mode_rec_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_rec_error_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_rec_proc_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_data_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_error_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_proc_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_tmp_dir).mkdir(parents=True, exist_ok=True)


def findFile(dirss, re_pat, age_secs):
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

    # Sort to ensure we try to process the files in order they were created
    files.sort()

    return files

#################################################################################
## Decoder related functions
#################################################################################

def archiveDecoderPidFile():
    now = datetime.now()
    dt_suffix = datetime.now().strftime("%Y%m%d_%H%M%S.%f")
    os.rename(decoder_pids_file, f"{decoder_pids_file}.{dt_suffix}")


def loadDecoderPid():

    if not os.path.exists(decoder_pids_file):
        return {}

    # CSV Format
    #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
    with open(decoder_pids_file, 'r') as file:
        rec = {}
        line = file.readline()
        rd = line.strip().split(",")

        pid = None
        if (rd[0] != "None"):
            pid = int(rd[0])

        rec["pid"] = pid
        rec["timestamp"] = rd[1]

    return rec

def checkStatusDecoder(pid):
    print(f"-- Status for js8Decoder process PID: {pid}")

    try:
        process = psutil.Process(pid)
        print(f"Process with PID {pid}:")
        print(f"  Name: {process.name()}")
        print(f"  Status: {process.status()}")
        # print(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
        # print(f"  Memory Info: {process.memory_info()}")
        print(f"  Command Line: {' '.join(process.cmdline())}")

        children = process.children(recursive=True)
        for child in children:
            print(f"  -- Child PID: {child.pid}, Name: {child.name()}, Status: {child.status()}")

    except psutil.NoSuchProcess:
        print(f"No process found with PID {pid}.")
    except psutil.AccessDenied:
        print(f"Access denied to process with PID {pid}.")

    return 0;

def checkDecoders(pid):
    print("Checking the status of the Decoding services...")
    
    rec = loadDecoderPid()

    if rec is not None and ('pid' not in rec):
        print(f"  -- No decoder processes are running. nothing to do.")
        sys.exit(0)

    pid = rec['pid']

    checkStatusDecoder(pid)

    return 0

def stopDecoder(pid):
    print(f"-- Shutting down js8decoder process PID: {pid}.")

    try:
        # Send SIGTERM (graceful termination request)
        os.kill(pid, signal.SIGTERM) 
        print(f"  -- Sent SIGTERM to process with PID {pid}")
    except ProcessLookupError:
        print(f"  -- ERROR: Process with PID {pid} not found.")
    except Exception as e:
        print(f"  -- ERROR: An error occurred: {e}")

    return 0;

def saveDecoderPid():

    now_utc = datetime.now(timezone.utc)
    
    pid = os.getpid()
    timestamp = int(now_utc.timestamp())

    # CSV Format
    #   <PID>,<timestamp>
    with open(decoder_pids_file, 'w') as file:
                    
            line = f"{pid}," + \
                f"{timestamp}\n"

            file.write(line)

    return 0


def stopDecoders(args):
    print("Stopping Decoding services...")
    
    rec = loadDecoderPid()
    
    if rec is not None and ('pid' not in rec):
        print(f"  -- No decoder processes are running. nothing to do.")
        sys.exit(0)

    pid = rec['pid']

    stopDecoder(pid)

    archiveDecoderPidFile()

    return 0

def logParsedDecodeMessages(parsedMsgs, log_fn):
    with open(decoder_pids_file, 'a') as file:

        for msg in parsedMsgs:
            file.write(str(msg))


def startDecoder(freq_khz, submode):

    now_utc = datetime.now(timezone.utc)

    freq_hz=freq_khz * 1000
    mode=submode['name']
    

    # TODO: Remove once we ensure thread safe using the setup call
    mode_root_dir = f"{data_dir}/{freq_hz}/{mode}"
    mode_rec_dir = f"{mode_root_dir}/rec"
    mode_rec_error_dir = f"{mode_rec_dir}/error"
    mode_rec_proc_dir = f"{mode_rec_dir}/done"
    mode_data_dir = f"{mode_root_dir}/data"
    mode_dec_dir = f"{mode_root_dir}/decode"
    mode_dec_error_dir = f"{mode_dec_dir}/error"
    mode_dec_proc_dir = f"{mode_dec_dir}/done"
    mode_tmp_dir = f"{data_dir}/{freq_hz}/{mode}/tmp"

    Path(mode_rec_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_rec_error_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_rec_proc_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_data_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_error_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_dec_proc_dir).mkdir(parents=True, exist_ok=True)
    Path(mode_tmp_dir).mkdir(parents=True, exist_ok=True)



    print(f"-- Starting js8Decoder process for Freq: [{freq_khz}] Mode: [{mode}] Folder: [{mode_rec_dir}]" )

    files = findFile(mode_rec_dir, r"\.wav$", 2)

    base_cmd = [JS8_BIN, 
            "-f", str(freq_hz),
            "--js8",
            "-b", submode["code"], 
            "-d", str(DEFAULT_DECODE_DEPTH), 
            "-a", mode_rec_dir, 
            "-t", mode_tmp_dir, 
        ]

    js8Parser = Js8Parser(freq_khz, "usb")

    for wav_fn in files:
        # JS8_CMD=" ${rec_dir}/${wavfn}"
        src_fn = f"{mode_rec_dir}/{wav_fn}"
        decode_fn = f"{wav_fn}.decode"
        decode_ffp = f"{mode_dec_dir}/{decode_fn}"

        print(f"-- JS8 decoding process started for file: [{src_fn}].")

        cmd = base_cmd + [src_fn]

        ret_code = None
        with open(decode_ffp, "w") as decode_log:

            # Start the process in a new session, detaching it from the current terminal
            process = subprocess.Popen(cmd,
                                    stdout=decode_log, 
                                    stderr=decode_log)

            ret_code = process.wait()

        if (ret_code and (ret_code != 0)):
            print(f"ERROR: Failed to decode wav file: {src_fn}  ReturnCode: [{ret_code}].") 
            os.rename(decode_ffp, f"{mode_dec_error_dir}/{decode_fn}")
        else: 
            tmp_decode_ffp = f"{mode_dec_proc_dir}/{decode_fn}"
            os.rename(decode_ffp, tmp_decode_ffp)

            # Decode using Js8Parser 
            parsedMsgs = js8Parser.processJs8DecodeFile(tmp_decode_ffp, None)

            if (parsedMsgs and (len(parsedMsgs) > 0)):
                print(f"DEBUG: Decode file: [{tmp_decode_ffp}] contained [{len(parsedMsgs)}] messages.")
                os.rename(tmp_decode_ffp, f"{mode_dec_proc_dir}/{decode_fn}")
            else:
                # Contrain no decoded message remove it
                os.remove(tmp_decode_ffp)

            logParsedDecodeMessages(parsedMsgs, f"{mode_data_dir}/all_parsed_decodes.txt")


        # Move wav file to processed / done folder
        os.rename(src_fn, f"{mode_rec_proc_dir}/{wav_fn}")

        #print(f"-- JS8 decoding process completed for file: [{src_fn}].")

    return 0;


def js8DecoderHandler(freq_khz, submode):
    print(f"js8Decoder handler prcessor started for Freq: [{freq_khz}] khz SubMode: [{submode['name']}]")

    # TODO: Confirm thread safe ?  for now will set local
    #setupSubmodeFolders(freq_khz, submode)

    while True:

        startDecoder(freq_khz, submode)

        print(f"  -- Sleeping for 15secs ...")
        time.sleep(15)


def startDecoders(args):

    
    print("Starting Recording services...")
    
    rec = loadDecoderPid()

    if rec is not None and ('pid' in rec):
        print(f"  -- WARNING: JS8 Decoding process PID: [{rec['pid']}] already started. Please review, perform a STOP then a START again.")
        sys.exit(-1)

    # Save current PPID
    saveDecoderPid()

    for freq in freq_list:
        for submode in submodes:
            
            # Create a Decoder Hanlding thread.
            #dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,), daemon=True)
            dh_thread = threading.Thread(target=js8DecoderHandler, args=(freq, submode,))
            dh_thread.start()
            #dh_thread.join()

    return 0
    
#####################################################################
## Recording Related functions
#####################################################################    

def startRecorder(freq_khz, submode):

    now_utc = datetime.now(timezone.utc)

    freq_hz=freq_khz * 1000
    mode=submode['name']

    setupSubmodeFolders(freq_khz, mode)

    rec = {
        "freq_khz": freq_khz,
        "freq_hz": freq_khz * 1000,
        "submode": mode,
        "submode_duration": submode["duration"],
        "mcast_addr": args.mcast_addr,
        "pid": None,
        # now_utc.isoformat() or Epoch/Timestamp ?
        "timestamp": int(now_utc.timestamp()),
        "ret_code": None
    }

    print(f"-- Starting new pcmrecord process for Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}]")

    # Determine SSRC
    freq_idx = FREQ_LIST.index(freq_khz)
    freq_ssrc = FREQ_SSRC[freq_idx]
    print(f"Selected SSRC: [{freq_ssrc}] for Freq: [{freq_khz}]")

    # IMPORTANT - USE Scott's WSPRDaemon version of "pcmrecord" to ensure that based on -L  will start correct time.
    cmd = [PCMRECORD_BIN, 
           "-L", str(submode["duration"]), 
           "-d", mode_rec_dir, 
           "-W", 
           "-S", 
           str(freq_ssrc), 
           "--jt", 
           args.mcast_addr]

    logfile = open(f"{mode_data_dir}/pcmrecord.log", "w")

    # Start the process in a new session, detaching it from the current terminal
    process = subprocess.Popen(cmd, start_new_session=True,
                            #    stdout=subprocess.PIPE, 
                            #    stderr=subprocess.PIPE)                               
                               stdout=logfile, 
                               stderr=logfile)
    
    rec["pid"] = process.pid
    rec["ret_code"] = process.returncode

    if (process.returncode and (process.returncode != 0)):
        print(f"ERROR: Command failed with return code: {process.returncode}")

    return rec;

def stopRecorder(rec):
    print(f"-- Shutting down pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

    pid = rec["pid"]

    try:
        # Send SIGTERM (graceful termination request)
        os.kill(rec['pid'], signal.SIGTERM) 
        print(f"  -- Sent SIGTERM to process with PID {pid}")
    except ProcessLookupError:
        print(f"  -- ERROR: Process with PID {pid} not found.")
    except Exception as e:
        print(f"  -- ERROR: An error occurred: {e}")

    return 0;

def checkStatusRecorder(rec):
    print(f"-- Status for pcmrecord process Freq: [{rec['freq_khz']}] Mode: [{rec['submode']}] PID: {rec['pid']}")

    pid = rec["pid"]

    try:
        process = psutil.Process(pid)
        print(f"Process with PID {pid}:")
        print(f"  Name: {process.name()}")
        print(f"  Status: {process.status()}")
        # print(f"  CPU Percent: {process.cpu_percent(interval=1.0)}%")
        # print(f"  Memory Info: {process.memory_info()}")
        print(f"  Command Line: {' '.join(process.cmdline())}")
    except psutil.NoSuchProcess:
        print(f"No process found with PID {pid}.")
    except psutil.AccessDenied:
        print(f"Access denied to process with PID {pid}.")

    return 0;

def loadRecordPids():

    if not os.path.exists(recorder_pids_file):
        return []

    # CSV Format
    #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
    with open(recorder_pids_file, 'r') as file:
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

    print(f"  - Loaded {len(recs)} records from recorder pids file.")

    return recs

def saveRecordPids(recs):

    # CSV Format
    #   <freq_khz>,<freq_hz>,<js8_submode>, <js8_submode_duration>,<mcast addr>,<PID>,<timestamp>,<retcode>
    with open(recorder_pids_file, 'w') as file:
        
        for rec in recs:
            
            line = f"{rec['freq_khz']}," + \
                f"{rec['freq_hz']}," + \
                f"{rec['submode']}," + \
                f"{rec['submode_duration']}," + \
                f"{rec['mcast_addr']}," + \
                f"{rec['pid']}," + \
                f"{rec['timestamp']}," + \
                f"{rec['ret_code']}\n"

            file.write(line)

    print(f"  - Saved {len(recs)} records from recorder pids file.")

    return recs


def startRecorders(args):
    print("Starting Recording services...")
    
    recs = loadRecordPids()
    recs_cnt = len(recs)
    if (recs_cnt > 0):
        print(f"  -- WARNING: There are {recs_cnt} PIDs already started. Please review, perform a STOP then a START again.")
        sys.exit(-1)

    recs = []

    for freq in freq_list:
        for submode in submodes:
            rec = startRecorder(freq, submode)
            recs.append(rec)

    saveRecordPids(recs)

    return 0

def archiveRecorderPidsFile():
    now = datetime.now()
    dt_suffix = datetime.now().strftime("%Y%m%d_%H%M%S.%f")
    os.rename(recorder_pids_file, f"{recorder_pids_file}.{dt_suffix}")

def stopRecorders(args):
    print("Stopping Recording services...")
    
    recs = loadRecordPids()

    recs_cnt = len(recs)
    if (recs_cnt == 0):
        print(f"  -- There are 0 records running. nothing to do.")
        sys.exit(0)

    for rec in recs:
        stopRecorder(rec)

    archiveRecorderPidsFile()

    return 0

def checkRecorders(args):
    print("Checking the status of the Recording services...")
    
    recs = loadRecordPids()

    recs_cnt = len(recs)
    if (recs_cnt == 0):
        print(f"  -- There are 0 records running. nothing to do.")
        sys.exit(0)


    for rec in recs:
        checkStatusRecorder(rec)

    return 0

########
## Main
########

def main():
    global args
    args = processArgs()

    print(f"Performing Process: [{args.process}] Action: [{args.action}]")

    if (args.process == "record"):
        if args.action == "start":
            startRecorders(args)
        elif args.action == "stop":
            stopRecorders(args)
        elif args.action == "status":
            checkRecorders(args)
        else:
            print(f"Unknown recording action: {args.command}")
            parser.print_help()

    elif (args.process == "decode"):
        if args.action == "start":
            startDecoders(args)
        elif args.action == "stop":
            stopDecoders(args)
        elif args.action == "status":
            checkDecoders(args)
        else:
            print(f"Unknown recording action: {args.command}")
            parser.print_help()

    else:
        print(f"Unknown process: {args.command} requested.")
        parser.print_help()

if __name__ == "__main__":
        main()