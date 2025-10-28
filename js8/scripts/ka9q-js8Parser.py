#!/usr/bin/python3

import sys
import argparse
import re
from datetime import datetime
from js8py import Js8
from js8py.frames import Js8FrameHeartbeat, Js8FrameCompound


decoderRegex = re.compile(" ?<Decode(Started|Debug|Finished)>")


def processArgs():

    parser = argparse.ArgumentParser(description="A simple script with arguments.")
    parser.add_argument("-dl", "--decode-line", type=str, help="raw js8 decoded line")
    parser.add_argument("-df", "--decode-file", type=str, help="Process a file that was generated from a single js8 decoded run output.")
    parser.add_argument("-f", "--freq", type=int, help="Radio Dial frequency in Hz")
    parser.add_argument("-m", "--mode", type=str, help="Radio Mode (usb / lsb).")
    parser.add_argument("-rt", "--record-time", type=str, help="Recording date/time for this decode. (expected ISO8601 format eg: 'YYYYMMDDTHHMMSSZ'). ")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    if (args.decode_line and args.decode_file):
        print("Warning: both MSG and FILE arguments provided, only file processing will be attempted.")
        sys.exit(-1)

    return args

def parse(params, raw_msg):
    try:
        msg = raw_msg.rstrip()
        if decoderRegex.match(msg):
            return
        if msg.startswith(" EOF on input file"):
            return

        frame = Js8().parse_message(msg)

        #print("-----------------------------------------------------------------")
        #print(dir(frame))

        is_spot = False
        if ((isinstance(frame, Js8FrameHeartbeat) or isinstance(frame, Js8FrameCompound)) and frame.grid):
            is_spot = True

        timestamp = int(datetime.fromisoformat(params["record_time"]).timestamp())

        out = {
            #"timestamp": frame.timestamp,
            "timestamp": timestamp,
            "mode": "JS8",
            "dial_freq": params["freq"],
            "offset": frame.freq,
            "freq": params["freq"] + frame.freq,
            "thread_type": frame.thread_type,
            "js8mode": frame.mode,
            "callsign": None,
            "locator": None,
            "callsign_to": None,
            "msg": str(frame),
            "db": frame.db,
            "dt": frame.dt,
            "spot": is_spot,
            "cmd": None,
            "snr": None,
            "frame_class": frame.__class__.__name__,
            "decode_file": params["decode_file"],
            "raw_msg": raw_msg,
        }


        if (hasattr(frame, 'callsign')):
            out["callsign"] = frame.callsign

        if (hasattr(frame, 'callsign_from')):
            out["callsign"] = frame.callsign_from
            
        if (hasattr(frame, 'callsign_to')):
            out["callsign_to"] = frame.callsign_to
            
        if (hasattr(frame, 'grid')):
            out["locator"] = frame.grid
            
        if (hasattr(frame, 'cmd')):
            out["cmd"] = frame.cmd
            
        if (hasattr(frame, 'snr')):
            out["snr"] = frame.snr
            
        return out

    except Exception as e:
        print(f"ERROR: error while parsing js8 message: [{msg}]. {e}")

# Determines if the first part of the firstname confirms to "--jt" option used by recording utlising (ie pcmrecord).
def processJTFilename(fn):
    # <DateTime_iso8601>_<DialfreqHz>_<mode>........
    # eg 20251026T192630Z_10130000_usb.wav
    rexpat = r"(\d{8}T\d{6}Z)_(\d{7,})_(usb|lsb).*"
    match = re.search(rexpat, fn)

    res = {}
    if match:
        
        res = {
#            "record_time": datetime.fromisoformat(match.group(1)),
            "record_time": match.group(1),
            "freq": int(match.group(2)),
            "mode": match.group(3)
        }

    res["decode_file"] = fn

    # Validate values and use commandline argument if provided:
    if ("freq" in res):
        if (args.freq and (res["freq"] != args.freq)):
            print(f"ERROR: Frequency specified via command line: [{args.freq}] and filename: [{res['freq']}] do not match. File: [{fn}] will be skipped.")
            sys.exit(-1)
    else:
        if (args.freq):
            res["freq"] = args.freq
        else:
            print("ERROR: Unable to determine frequency. Please ensure to specify frequency as argument, or ensure filename is supplied using expected naming convention.")
            sys.exit(-1)

    if ("mode" in res):
        if (args.mode and (res["mode"] != args.mode)):
            print(f"ERROR: Mode specified via command line: [{args.mode}] and filename: [{res['mode']}] do not match. File: [{fn}] will be skipped.")
            sys.exit(-1)
    else:
        if (args.mode):
            res["mode"] = args.mode
        else:
            print("ERROR: Unable to determine mode (ie usb/lsb). Please ensure to specify mode as argument, or ensure filename is supplied using expected naming convention.")
            sys.exit(-1)

    if ("record_time" in res):
        if (args.record_time and (res["record_time"] != args.record_time)):
            print(f"ERROR: Recording date/time specified via command line: [{args.record_time}] and filename: [{res['record_time']}] do not match. File: [{fn}] will be skipped.")
            sys.exit(-1)
    else:
        if (args.record_time):
            res["record_time"] = args.record_time
        else:
            print("ERROR: Unable to determine recording date/time. Please ensure to specify recording date/time as argument, or ensure filename is supplied using expected naming convention.")
            sys.exit(-1)

    return res



def processJs8DecodeLine(msg):
    frame = Js8().parse_message(msg)
    print(str(frame))

    print("-----------------------------------------------------------------")
    print(dir(frame))



# 
def processJs8DecodeFile(js8decode_fn):
    params = processJTFilename(js8decode_fn)

    # Open and process the file line by line
    try:
        with open(js8decode_fn, "r") as file:
            for line in file:
                out = parse(params, line)
                if out:
                    #print(f"-- [{js8decode_fn}]")
                    print(out)
    except FileNotFoundError:
        print("Error: The file '",js8decode_fn,"' was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

    return 0

########
## Main
########

args = processArgs()

if (args.decode_file):
    processJs8DecodeFile(args.decode_file)
elif (args.decode_line):
    processJs8DecodeLine(args.decode_line)
else:
    print("Nothing to do!  Did you specify either FILE or MSG option?")
