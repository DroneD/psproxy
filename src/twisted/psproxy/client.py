from twisted.internet import defer
from twisted.python import failure
from twisted.spread import pb

_machine_methods = [
    "process_list",
    "pid_exists",
    "phymem_usage",
    "phymem_buffers",
    "disk_io_counters",
    "cpu_percent",
    "network_io_counters",
    "find_processes",
]
_process_methods = {
    "get_connections": "process_get_connections",
    "get_cpu_percent": "process_get_cpu_percent",
    "get_cpu_times": "process_get_cpu_times",
    "get_io_counters": "process_get_io_counters",
    "get_ionice": "process_get_ionice",
    "get_memory_info": "process_get_memory_info",
    "get_memory_percent": "process_get_memory_percent",
    "get_open_files": "process_get_open_files",
    "get_threads": "process_get_threads",
    "getcwd": "process_getcwd",
    "is_running": "process_is_running",
}


class MachineConnector(object):
    connected = property(lambda s: s._deferred.called and not s._error)
    machine = property(lambda s: getattr(s, '_object', s._error))
    error = property(lambda s: s._error)

    def __init__(self, reactor, hostname='localhost', port=666):
        self._error = None
        self.hostname = hostname
        self.port = port
        factory = pb.PBClientFactory()
        reactor.connectTCP(hostname, port, factory)
        self._deferred = factory.getRootObject()
        self._deferred.addCallback(
            lambda x: setattr(self, '_object', x) and x or x)
        self._deferred.addErrback(
            lambda x: setattr(self, '_error', x) and x or x)

    def __call__(self, method):
        def remote_call(*args, **kwargs):
            if self.error:
                return defer.fail(self.error)
            elif self._deferred.called and getattr(self, '_object', None):
                return self._object.callRemote(method, *args, **kwargs)
            return self._deferred.addCallback(
                lambda x: x.callRemote(method, *args, **kwargs))
        remote_call.__doc__ = """Remote process proxy call %s""" % (method,)
        return remote_call

    def __getattribute__(self, *args):
        name = args[0]
        if name in _machine_methods:
            return self(name) 
        return object.__getattribute__(self, *args)


class Process(object):
    connected = property(lambda s: s._original.connected)
    machine = property(lambda s: s._original.machine)
    error = property(lambda s: s._original.error)
    pid = property(lambda s: s._pid)

    def __init__(self, pid, machine=None):
        if not machine:
            from twisted.internet import reactor
            self._original = MachineConnector(reactor)
        else:
            self._original = machine
        self._pid = int(pid)

    def __call__(self, method):
        def call_wrapper(*args, **kwargs):
            args = (self.pid,) + args
            return self._original(method)(*args, **kwargs)
        return call_wrapper

    def __getattribute__(self, *args):
        name = args[0]
        if name in _process_methods:
            return self(_process_methods[name]) 
        return object.__getattribute__(self, *args)


if __name__ == '__main__':
    from twisted.internet import reactor
    from twisted.python.util import println
    import traceback

    @defer.inlineCallbacks
    def _test():
        agent = MachineConnector(reactor)
        foo = yield agent.process_list()
        println(foo)
        for pid in foo:
            try:
                x = yield Process(pid, agent).get_connections()
                if not x: continue
                println(x)
            except:
                traceback.print_exc()
        #        break

    def test():
        d = _test()
        d.addErrback(lambda x: x.printTraceback() and x)
        d.addBoth(lambda x: reactor.stop())
        return d

    reactor.callWhenRunning(test)
    reactor.run()
