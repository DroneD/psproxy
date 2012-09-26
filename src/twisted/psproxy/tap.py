###############################################################################
#   Copyright 2012, DroneD Project.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
###############################################################################

"""
Support for creating a service that exposes system processes.
"""

__author__ = "Justin Venus <justin.venus@gmail.com>"

#TODO/FIXME security!!!!!

from twisted.cred import credentials, portal, strcred
from twisted.python import usage, util
from twisted.internet import defer
from twisted.application import internet, service
from twisted.plugin import IPlugin
from twisted.spread import pb
from twisted.psproxy import process
from zope.interface import implements
import copy
import re

SERVICE = None

def extractData(func):
    def extract(result):
        if isinstance(result, list):
            newList = []
            for item in result:
                newList.append(extract(item))
            return newList
        if isinstance(result, dict):
            newDict = {}
            for var, val in result.items():
                newDict[var] = extract(val)
            return newDict
        if hasattr(result, '__dict__'):
            return dict(result.__dict__)
        return result
    def decorator(self, *args, **kwargs):
        d = defer.maybeDeferred(func, self, *args, **kwargs)
        d.addCallback(extract)
        return d
    return decorator

class ProcessRoot(pb.Root, object):
    """Provides the System Information Remote API"""
    ###########################################################################
    # System Wide API is defined in this block
    ###########################################################################
    def remote_process_list(self):
        return self.server.process_cache.keys()

    def remoteMessageReceived(self, *args, **kwargs):
        """update process cache on message received."""
        if not self.server.deferred.called:
            self.server.scheduler()
        return pb.Root.remoteMessageReceived(self, *args, **kwargs)

    def remote_pid_exists(self, pid):
        return process.pid_exists(pid)

    @extractData
    def remote_phymem_usage(self):
        return process.phymem_usage()

    @extractData
    def remote_phymem_buffers(self):
        return process.phymem_buffers()

    @extractData
    def remote_disk_io_counters(self):
        return process.disk_io_counters()

    @extractData
    def remote_cpu_percent(self, interval=0.1, percpu=False):
        return process.cpu_percent(interval=interval,percpu=percpu)

    @extractData
    def remote_network_io_counters(self, pernic=False):
        return process.network_io_counters(pernic=pernic)

    @defer.inlineCallbacks
    def remote_find_processes(self, regex='.*'):
        regex = re.compile(regex)
        possible = yield self.server.scheduler()
        result = []
        for var, val in possible.items():
            try:
                if regex.search(' '.join(val.cmdline)):
                    result.append(var) 
            except: continue
        defer.returnValue(result)
    ###########################################################################
    # Process API is defined in this block
    ###########################################################################
    @extractData
    def remote_process_get_connections(self, pid, kind='inet'):
        return process.Process(pid).get_connections(kind=kind)

    def remote_process_get_cpu_percent(self, pid, interval=0.1):
        return process.Process(pid).get_cpu_percent(interval=interval)

    @extractData
    def remote_process_get_cpu_times(self, pid):
        return process.Process(pid).get_cpu_times()

    @extractData
    def remote_process_get_io_counters(self, pid):
        return process.Process(pid).get_io_counters()

    @extractData
    def remote_process_get_ionice(self, pid):
        return process.Process(pid).get_ionice()

    @extractData
    def remote_process_get_memory_info(self, pid):
        return process.Process(pid).get_memory_info()
    
    def remote_process_get_memory_percent(self, pid):
        return process.Process(pid).get_memory_percent()

    def get_num_threads(self, pid):
        return process.Process(pid).get_num_threads()

    @extractData
    def remote_process_get_open_files(self, pid):
        return process.Process(pid).get_open_files()

    @extractData
    def remote_process_get_threads(self, pid):
        return process.Process(pid).get_threads()

    def remote_process_getcwd(self, pid):
        return process.Process(pid).getcwd()

    def remote_process_is_running(self, pid):
        return process.Process(pid).is_running()

#provide a convienent way to look up the server resource from the root.
ProcessRoot = type(
    'ProcessRoot', (ProcessRoot,), {'server': property(lambda s: SERVICE)})

class ProcessServer(internet.TCPServer, object):
    process_cache = {}
    deferred = property(lambda s: s._deferred)
    def __init__(self, *args, **kwargs):
        internet.TCPServer.__init__(self, *args, **kwargs)
        self._deferred = defer.succeed(None)
        self.scheduler() #preload cache

    def scheduler(self):
        if self._deferred.called:
            self._deferred = self._process_scanner()
            self._deferred.addCallback(self._updateState)
        return self._deferred

    def _updateState(self, resultDict):
        self.process_cache = copy.deepcopy(resultDict)
        return resultDict

    @process.threaded
    def _process_scanner(self):
        results = {}
        for proc in process.process_iter():
            try:
                results.update({proc.pid: proc})
            except: continue
        return results


class Options(usage.Options, strcred.AuthOptionMixin):
    supportedInterfaces = (credentials.IUsernamePassword,)
    optParameters = [
        ["port", "p", 666, "Server port number", usage.portCoerce],
        ["host", "h", "localhost", "Server hostname"],
    ]


def makeService(options):
    global SERVICE
    factory = pb.PBServerFactory(ProcessRoot())
    SERVICE = ProcessServer(options["port"], factory)
    return SERVICE
