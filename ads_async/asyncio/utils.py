import asyncio
import functools
import inspect
import sys
import threading
import weakref


class _TaskHandler:
    # Borrowed from caproto

    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def create(self, coro):
        """Schedule the execution of a coroutine object in a spawn task."""
        task = create_task(coro)
        with self._lock:
            self.tasks.append(task)
        task.add_done_callback(self._remove_completed_task)
        return task

    def _remove_completed_task(self, task):
        try:
            with self._lock:
                self.tasks.remove(task)
        except ValueError:
            # May have been cancelled or removed otherwise
            ...

    async def cancel(self, task):
        task.cancel()
        await task
        # _remove_completed_task will handle updating `self.tasks`

    async def cancel_all(self, wait=False):
        with self._lock:
            tasks = list(self.tasks)
            self.tasks.clear()

        for task in tasks:
            task.cancel()

        if wait and tasks:
            await asyncio.wait(tasks)

    async def wait(self):
        with self._lock:
            tasks = list(self.tasks)

        if tasks:
            await asyncio.wait(tasks)


class AsyncioQueue:
    '''Asyncio queue modified for async/sync API compatibility.'''

    def __init__(self, maxsize=0):
        self._queue = asyncio.Queue(maxsize)

    async def async_get(self):
        return await self._queue.get()

    async def async_put(self, value):
        return await self._queue.put(value)

    def get(self):
        future = asyncio.run_coroutine_threadsafe(
            self._queue.get(), get_running_loop())

        return future.result()

    def put(self, value):
        self._queue.put_nowait(value)


class CallbackExecutor:
    def __init__(self, log):
        self.callbacks = AsyncioQueue()
        self.tasks = _TaskHandler()
        self.tasks.create(self._callback_loop())
        self.log = log

    async def shutdown(self):
        await self.tasks.cancel_all()

    async def _callback_loop(self):
        loop = get_running_loop()

        while True:
            callback, args, kwargs = await self.callbacks.async_get()
            if inspect.iscoroutinefunction(callback):
                try:
                    await callback(*args, **kwargs)
                except Exception:
                    self.log.exception('Callback failure')
            else:
                try:
                    loop.run_in_executor(
                        None, functools.partial(callback, *args, **kwargs))
                except Exception:
                    self.log.exception('Callback failure')

    def submit(self, callback, *args, **kwargs):
        self.callbacks.put((callback, args, kwargs))


class CallbackHandler:
    def __init__(self, notification_id, handle, user_callback_executor):
        # NOTE: not a WeakValueDictionary or WeakSet as PV is unhashable...
        self.callbacks = {}
        self.handle = handle
        self.notification_id = notification_id
        self._callback_id = 0
        self.callback_lock = threading.RLock()
        self.user_callback_executor = user_callback_executor
        self._last_call_values = None

    def add_callback(self, func, run=False):
        def removed(_):
            self.remove_callback(cb_id)  # defined below inside the lock

        if inspect.ismethod(func):
            ref = weakref.WeakMethod(func, removed)
        else:
            # TODO: strong reference to non-instance methods?
            ref = weakref.ref(func, removed)

        with self.callback_lock:
            cb_id = self._callback_id
            self._callback_id += 1
            self.callbacks[cb_id] = ref

        if run and self._last_call_values is not None:
            with self.callback_lock:
                args, kwargs = self._last_call_values
            self.process(*args, **kwargs)
        return cb_id

    def remove_callback(self, token):
        # TODO: async confusion:
        #       sync CallbackHandler.remove_callback
        #       async Subscription.remove_callback
        with self.callback_lock:
            self.callbacks.pop(token, None)

    def process(self, *args, **kwargs):
        """
        This is a fast operation that submits jobs to the Context's
        ThreadPoolExecutor and then returns.
        """
        to_remove = []
        with self.callback_lock:
            callbacks = list(self.callbacks.items())
            self._last_call_values = (args, kwargs)

        for cb_id, ref in callbacks:
            callback = ref()
            if callback is None:
                to_remove.append(cb_id)
                continue

            self.user_callback_executor.submit(
                callback, *args, **kwargs
            )

        with self.callback_lock:
            for remove_id in to_remove:
                self.callbacks.pop(remove_id, None)


if sys.version_info < (3, 7):
    # python <= 3.6 compatibility
    def get_running_loop():
        return asyncio.get_event_loop()

    def run(coro, debug=False):
        return get_running_loop().run_until_complete(coro)

    def create_task(coro):
        return get_running_loop().create_task(coro)

else:
    get_running_loop = asyncio.get_running_loop
    run = asyncio.run
    create_task = asyncio.create_task
