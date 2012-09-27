#!/usr/bin/twistd -y
from twisted.cred import credentials, portal, strcred
from twisted.python import usage, util
from twisted.internet import defer
from twisted.application import internet, service
from twisted.plugin import IPlugin
from twisted.spread import pb
from twisted.psproxy import process
from zope.interface import implements
import time
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

@process.threaded
def proc(pid):
    return process.Process(pid)

class ProcessRoot(pb.Root, object):
    """Provides the System Information Remote API"""
    ###########################################################################
    # System Wide API is defined in this block
    ###########################################################################
    def remote_process_list(self):
        return self.server.process_cache.keys()

#    def remoteMessageReceived(self, *args, **kwargs):
#        x = self.server.scheduler()
#        return pb.Root.remoteMessageReceived(self, *args, **kwargs)

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
    @defer.inlineCallbacks
    def remote_process_get_connections(self, pid, kind='inet'):
        obj = yield proc(pid)
        obj = yield obj.get_connections(kind=kind)
        defer.returnValue(obj)

    @defer.inlineCallbacks
    def remote_process_get_cpu_percent(self, pid, interval=0.1):
        return process.Process(pid).get_cpu_percent(interval=interval)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_cpu_times(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_cpu_times()
        defer.returnValue(obj)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_io_counters(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_io_counters()
        defer.returnValue(obj)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_ionice(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_ionice()
        defer.returnValue(obj)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_memory_info(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_memory_info()
        defer.returnValue(obj)
    
    @defer.inlineCallbacks
    def remote_process_get_memory_percent(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_memory_percent()
        defer.returnValue(obj)

    @defer.inlineCallbacks
    def get_num_threads(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_num_threads()
        defer.returnValue(obj)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_open_files(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_open_files()
        defer.returnValue(obj)

    @extractData
    @defer.inlineCallbacks
    def remote_process_get_threads(self, pid):
        obj = yield proc(pid)
        obj = yield obj.get_threads()
        defer.returnValue(obj)

    @defer.inlineCallbacks
    def remote_process_getcwd(self, pid):
        obj = yield proc(pid)
        obj = yield obj.getcwd()
        defer.returnValue(obj)

    @defer.inlineCallbacks
    def remote_process_is_running(self, pid):
        obj = yield proc(pid)
        obj = yield obj.is_running()
        defer.returnValue(obj)

#provide a convienent way to look up the server resource from the root.
ProcessRoot = type(
    'ProcessRoot', (ProcessRoot,), {'server': property(lambda s: SERVICE)})

class ProcessServer(internet.TCPServer, object):
    process_cache = {}
    deferred = property(lambda s: s._deferred)
    def __init__(self, *args, **kwargs):
        internet.TCPServer.__init__(self, *args, **kwargs)
        self._deferred = defer.succeed({})
        self.scheduler() #preload cache
        self.timestamp = 0

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
