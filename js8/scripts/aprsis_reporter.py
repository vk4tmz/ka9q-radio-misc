#!/usr/bin/env python

###############
##
##  Dependencies:
##
##  python3 -m pip install aprs3 maidenhead
##
###############

import aprs
import re
import logging
import maidenhead as mh

from math import modf
from ka9q_js8Utils import writeStringToFile

CALLSIGN_SUFFIX_REX = r"(?P<callsign>[\d\w]+)[/]?(?P<suffix>[\d\w]+)?"

DEFAULT_APRS_HOST="asia.aprs2.net"
DEFAULT_APRS_PORT=14580
DEFAULT_LOG_FN="./aprs_frames.log"

logger = logging.getLogger(__name__)

class APRSReporter:

    reporter: str
    aprs_reporting_enabled: bool
    aprs_user: str
    aprs_passcode: str
    aprs_host: str
    aprs_port: int

    log_fn: str;

    def __init__(self, reporter:str, user:str, passcode:str, reporting_enabled:bool=True, host:str=DEFAULT_APRS_HOST, port:int=DEFAULT_APRS_PORT, log_fn: str=DEFAULT_LOG_FN):
        self.reporter = reporter.upper()
        self.log_fn = log_fn
        self.aprs_reporting_enabled = reporting_enabled
        self.aprs_host=host
        self.aprs_port=port
        self.aprs_user=user
        self.aprs_passcode=passcode

        logger.info(f"DEBUG: APRSReport's log file: [{self.log_fn}]")


    def grid2aprs(self, grid_locator: str):
        # Get the top-left coordinates (latitude, longitude)
        lat, lon = mh.to_location(grid_locator)
        #print(f"Top-left corner: Lat={lat}, Lon={lon}")

        # Get the center coordinates (latitude, longitude)
        center_lat, center_lon = mh.to_location(grid_locator, center=True)
        #print(f"Center of square: Lat={center_lat}, Lon={center_lon}")

        latDir = "N"
        if (lat < 0):
            lat *= -1
            latDir = "S"
        
        lonDir = "E"
        if (lon < 0):
            lon *= -1
            lonDir = "W"

        fLat,iLat = modf(lat)
        fLon,iLon = modf(lon)

        fLatMin,iLatMin = modf(fLat * 60)
        fLonMin,iLonMin = modf(fLon * 60)

        iLatSec = round(fLatMin * 60)
        iLonSec = round(fLonMin * 60)

        if (iLatSec == 60):
            iLatMin += 1
            iLatSec = 0

        if (iLonSec == 60):
            iLonMin += 1
            iLonSec = 0
        
        if (iLatMin == 60):
            iLat += 1
            iLatMin = 0

        if (iLonMin == 60):
            iLon += 1
            iLonMin = 0

        aprsLat = iLat * 100 + iLatMin + (iLatSec / 60.0)
        aprsLon = iLon * 100 + iLonMin + (iLonSec / 60.0)

        return f"{aprsLat:07.2f}{latDir}", f"{aprsLon:08.2f}{lonDir}"  


    ############################################################################

    def removeCallsignSuffix(self, callsign: str):
        match = re.match(CALLSIGN_SUFFIX_REX, callsign)

        if match:
            return match.group(1)
        else:
            return callsign

    def sendFrame(self, frame):
        with aprs.TCP(user=self.aprs_host, passcode=self.aprs_passcode, host=self.aprs_host, port=self.aprs_port) as a:
            logger.debug(f"Reporting APRS Frame: [{frame}].")
            if self.aprs_reporting_enabled:
                writeStringToFile(self.log_fn, f"{str(frame)}\n", True)
                # a.write(frame)


    def reportAprsPosition(self, callsign: str, grid_locator:str, comment: str):

        lat,lon = self.grid2aprs(grid_locator)

        # Format APRS Position report
        # msg = f"{callsign_nosuffix}>APJ8CL,qAS,{reporter}:={lat}/{lon}G#{comment}"
        msg = f"={lat}/{lon}G#{comment}"
        self.reportAprsMessage(callsign, msg)

    def reportAprsMessage(self, callsign: str, msg: str):

        callsign_nosuffix = self.removeCallsignSuffix(callsign)

        # Format APRS Position report
        frame_msg = f"{callsign_nosuffix.upper()}>APJ8CL,qAS,{self.reporter}:{msg}"
        logger.info(f"DEBUG: frame: [{frame_msg}]")
        frame = aprs.APRSFrame.from_str(frame_msg)

        self.sendFrame(frame)


#########################

