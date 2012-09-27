#!/usr/bin/twistd -y
from twisted.cred import credentials, portal, strcred
from twisted.python import usage, util, failure
from twisted.internet import defer, task
from twisted.application import internet
from twisted.spread import pb
from twisted.psproxy import process
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
    def extractFailure(reason):
        return failure.Failure(
            pb.RemoteError(reason.type, reason.value, reason.printTraceback))
    def decorator(self, *args, **kwargs):
        d = defer.maybeDeferred(func, self, *args, **kwargs)
        d.addCallback(extract)
        d.addErrback(extractFailure)
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
    def proc(self, pid):
        return self.server.process_cache[pid]

    @extractData
    def remote_process_list(self):
        return self.server.process_cache.keys()

    @extractData
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

    @extractData
    def remote_find_processes(self, regex='.*'):
        regex = re.compile(regex)
        result = []
        for var, val in self.server.process_cache.items():
            try:
                if regex.search(' '.join(val.cmdline)):
                    result.append(var) 
            except: continue
        return result
    ###########################################################################
    # Process API is defined in this block
    ###########################################################################
    @extractData
    def remote_process_get_connections(self, pid, kind='inet'):
        return proc(pid).addCallback(
            lambda x: x.get_connections(kind=kind))

    @extractData
    def remote_process_get_cpu_percent(self, pid, interval=0.1):
        return proc(pid).addCallback(
            lambda x: x.get_cpu_percent(interval=interval))

    @extractData
    def remote_process_get_cpu_times(self, pid):
        return proc(pid).addCallback(lambda x: x.get_cpu_times())

    @extractData
    def remote_process_get_io_counters(self, pid):
        return proc(pid).addCallback(lambda x: x.get_io_counters())

    @extractData
    def remote_process_get_ionice(self, pid):
        return proc(pid).addCallback(lambda x: x.get_ionice())

    @extractData
    def remote_process_get_memory_info(self, pid):
        return proc(pid).addCallback(lambda x: x.get_memory_info())
    
    @extractData
    def remote_process_get_memory_percent(self, pid):
        return proc(pid).addCallback(lambda x: x.get_memory_percent())

    @extractData
    def get_num_threads(self, pid):
        return proc(pid).addCallback(lambda x: x.get_num_threads())

    @extractData
    def remote_process_get_open_files(self, pid):
        return proc(pid).addCallback(lambda x: x.get_open_files())

    @extractData
    def remote_process_get_threads(self, pid):
        return proc(pid).addCallback(lambda x: x.get_threads())

    @extractData
    def remote_process_getcwd(self, pid):
        return proc(pid).addCallback(lambda x: x.getcwd())

    @extractData
    def remote_process_is_running(self, pid):
        return proc(pid).addCallback(lambda x: x.is_running())

#provide a convienent way to look up the server resource from the root.
ProcessRoot = type(
    'ProcessRoot', (ProcessRoot,), {'server': property(lambda s: SERVICE)})


def synchronizedDeferred(lock):
    """The function will run with the given lock acquired"""
    #make sure we are given an acquirable/releasable object
    assert isinstance(lock, (defer.DeferredLock, defer.DeferredSemaphore))
    def decorator(func):
        """Takes a function that will aquire a lock, execute the function, 
           and release the lock.  Control will then be returned to the 
           reactor for err/callbacks to run.
  
           returns deferred
        """
        def newfunc(*args,**kwargs):
            return lock.run(func,*args,**kwargs)
        return newfunc
    return decorator


class ProcessServer(internet.TCPServer, object):
    Lock = defer.DeferredLock()
    factory = None
    process_cache = {}
    deferred = property(lambda s: s._deferred)
    def __init__(self, *args, **kwargs):
        self.interval = kwargs.pop("interval")
        internet.TCPServer.__init__(self, *args, **kwargs)
        self._deferred = defer.succeed({})
        self._task = task.LoopingCall(self.scheduler)
        self._task.start(self.interval)

    @synchronizedDeferred(Lock)
    def _restartTask(self):
        if self._task.running:
            self._task.stop()
        self._task.start(self.interval)
        from twisted.internet import reactor
        d = defer.Deferred()
        reactor.callLater(self.interval/2.0, d.callback, None)
        return d #silly hack to spread out re-scans

    def restartTask(self):
        if self.Lock.locked or not self.deferred.called:
            return defer.succeed(None)
        return self._restartTask()

    def scheduler(self):
        if hasattr(self.factory, 'allConnections'):
            if not self.factory.allConnections:
                return defer.succeed(self.process_cache)
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
        for p in process.process_iter():
            try:
                results.update({p.pid: p})
            except: continue
        return results


class NotifyingPBServerFactory(pb.PBServerFactory):
    allConnections = set()
    def buildProtocol(self, addr):
        protocol = pb.PBServerFactory.buildProtocol(self, addr)
        protocol.notifyOnConnect(lambda: self.allConnections.add(addr)) 
        protocol.notifyOnConnect(self.server.restartTask)
        protocol.notifyOnDisconnect(lambda: self.allConnections.discard(addr))
        return protocol


class Options(usage.Options, strcred.AuthOptionMixin):
    supportedInterfaces = (credentials.IUsernamePassword,)
    optParameters = [
        ["port", "p", 666, "Server port number", usage.portCoerce],
        ["host", "h", "localhost", "Server hostname"],
        ["interval", "", 15, "Interval to scan processes", float],
    ]


def makeService(options):
    global SERVICE
    factory = NotifyingPBServerFactory(ProcessRoot())
    SERVICE = ProcessServer(
        options["port"], factory, interval=options["interval"])
    factory.server = SERVICE
    SERVICE.factory = factory
    return SERVICE
