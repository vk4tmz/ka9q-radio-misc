
import logging
import json
import os
import re
import shutil
import sys
import time

from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_METHOD_MOVE="AMM"
ARCHIVE_METHOD_TRUNCATE="AMT"

logger = logging.getLogger(__name__)

def logError(msg: str, exit_code:int=None, logger=logger):
    logger.error(msg)
    if (exit_code is not None):
        sys.exit(exit_code)

def isEmpty(s:str):
    return (s is None) or (len(s)==0)

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