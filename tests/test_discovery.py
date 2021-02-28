import asyncio
import socket
import unittest
import unittest.mock
import tracemalloc

tracemalloc.start()

from maxcube.discovery import Explorer
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, call, patch

BROADCAST_MSG = b'eQ3Max*\x00**********I'
BROADCAST_ADDRESS = ('<broadcast>', 23272)
CUBE_ADDRESS = ('192.168.1.8', 23272)

class TestDiscovery(TestCase):

    def setUp(self):
        self.addCleanup(patch.stopall)
        self.asyncio_get_running_loop_mock = patch('asyncio.get_running_loop', spec=True).start()
        self.loop = self.asyncio_get_running_loop_mock.return_value
        self.explorer = Explorer()
        self.sender_task = None
        self.receiver_task = None

    def tearDown(self):
        # Avoid warnings about un-awaited coroutines
        if self.sender_task:
            self.sender_task.close()
        if self.receiver_task:
            self.receiver_task.close()

    def testDiscoveryStartAndStop(self):
        _run(self._start_stop())

    async def _start_stop(self):
        await self._start()
        self.assertEqual(self.stream, self.explorer.stream)
        self.assertEqual(self.loop, self.explorer.loop)
        await self.explorer.async_stop()
        self.stream.close.assert_called_once()
        self.assertIsNone(self.explorer.stream)
        self.assertIsNone(self.explorer.events)
        self.assertIsNone(self.explorer.loop)

    def testDiscoveryStopsAfterFailedCreateTask(self):
        sendTask = asyncio.Future()
        self.loop.create_task.side_effect = [sendTask, RuntimeError("SomeError")]
        self.assertRaises(RuntimeError, _run, self._start())
        self.assertTrue(sendTask.cancelled())
        self.stream.close.assert_called_once()
        self.assertIsNone(self.explorer.stream)
        self.assertIsNone(self.explorer.events)
        self.assertIsNone(self.explorer.loop)

    async def _start(self):
        socket_mock = patch('socket.socket', spec=True).start()
        from_socket_mock = patch('asyncio_dgram.from_socket', spec=True).start()
        # Fix warning about not waiting at stream close
        from_socket_mock.return_value.close = Mock()

        try:
            await self.explorer.async_start()
        finally:
            self.sender_task = self.loop.create_task.call_args_list[0].args[0]
            self.receiver_task = self.loop.create_task.call_args_list[1].args[0]

            self.assertTrue(self.asyncio_get_running_loop_mock.called)
            self.assertEqual(2, self.loop.create_task.call_count)

            socket_mock.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s = socket_mock.return_value
            s.setsockopt.assert_has_calls([
                call(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1),
                call(socket.SOL_SOCKET, socket.SO_BROADCAST, 1),
            ])
            s.bind.assert_called_once_with(('', 23272))

            from_socket_mock.assert_called_once_with(s)
            self.stream = from_socket_mock.return_value

    def testDiscoveryStopsAfterFailingAtOpenStream(self):
        self.assertRaises(RuntimeError, _run, self._start_failed_socket())
        self.assertIsNone(self.explorer.stream)
        self.assertIsNone(self.explorer.events)
        self.assertIsNone(self.explorer.loop)

    async def _start_failed_socket(self):
        socket_mock = patch('socket.socket', spec=True).start()
        socket_mock.side_effect = [RuntimeError("Test")]
        try:
            await self.explorer.async_start()
        finally:
            self.assertTrue(self.asyncio_get_running_loop_mock.called)

    def testDiscoverySender(self):
        _run(self._discovery_sender())

    async def _discovery_sender(self):
        await self._start()
        sleep_mock = patch('asyncio.sleep', spec=True).start()
        sleep_mock.side_effect = [_resolved_future(None), RuntimeError('EndTest')]
        try:
            await self.sender_task
        except RuntimeError as ex:
            self.assertEqual(ex.args[0], 'EndTest')

        self.stream.send.assert_has_calls([
            call(BROADCAST_MSG, BROADCAST_ADDRESS),
            call(BROADCAST_MSG, BROADCAST_ADDRESS)
        ])
        sleep_mock.assert_has_calls([call(60), call(60)])

    def testDiscoveryReceiver(self):
        _run(self._discovery_receiver())

    async def _discovery_receiver(self):
        await self._start()
        self.stream.recv.side_effect = [
            # Bad input: too short
            (bytes.fromhex('6551334d617841704b4d44313032383230343e49000abc1401'), CUBE_ADDRESS),
            # Bad input: bad magic code
            (bytes.fromhex('6551324d617841704b4d44313032383230343e49000abc140113'), CUBE_ADDRESS),
            # Bad input: bad command
            (bytes.fromhex('6551334d617841704b4d44313032383230343e48000abc140113'), CUBE_ADDRESS),
            # Good input
            (bytes.fromhex('6551334d617841704b4d44313032383230343e49000abc140113'), CUBE_ADDRESS),
            RuntimeError('EndTest')
        ]
        try:
            await self.receiver_task
        except RuntimeError as ex:
            self.assertEqual(ex.args[0], 'EndTest')

        cube = await self.explorer.async_next_update()
        self.assertEqual(cube.address, CUBE_ADDRESS[0])
        self.assertEqual(cube.serial, 'KMD1028204')
        self.assertEqual(cube.rf_address, '0abc14')
        self.assertEqual(cube.version, '1.1.3')

        try:
            await asyncio.wait_for(self.explorer.async_next_update(), 0.01)
            self.fail('Unexpected message in the input queue')
        except asyncio.TimeoutError:
            pass



def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)

def _resolved_future(value):
    f = asyncio.Future()
    f.set_result(value)
    return f
