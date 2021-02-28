import asyncio
import asyncio_dgram
import logging
import socket
import time

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from .cube import MaxCube

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 23272
BIND_ADDRESS = ('', DISCOVERY_PORT)
BROADCAST_ADDRESS = ('<broadcast>', DISCOVERY_PORT)
BROADCAST_SERIAL = '*' * 10
DISCOVERY_MAGIC = b'eQ3Max'
DISCOVERY_HEADER = DISCOVERY_MAGIC + b'*\0'
DISCOVERY_CMD = b'I'
REBOOT_CMD = b'R'

@dataclass(frozen=True)
class DiscoveredCube:
    serial: str
    address: str
    rf_address: str
    version: str


class Explorer:

    def __init__(self, *, broadcast_period: float = 60):
        self.loop: asyncio.AbstractEventLoop = None
        self.broadcast_period: float = broadcast_period
        self.events: asyncio.Queue = None
        self.cubes: Dict[str, DiscoveredCube] = {}
        self.sender_task: asyncio.Task = None
        self.receiver_task: asyncio.Task = None
        self.stream = None

    async def async_start(self):
        if self.loop is not None:
            raise RuntimeError("Discovery already in progress")
        self.loop = asyncio.get_running_loop()
        if self.loop is None:
            raise RuntimeError("No asyncio running loop")

        try:
            self.events = asyncio.Queue()
            self.stream = await Explorer.__open_stream(broadcast=True)
            self.sender_task = self.loop.create_task(self.__sender())
            self.receiver_task = self.loop.create_task(self.__receiver())
        except:
            await self.async_stop()
            raise

    async def async_stop(self):
        if self.loop is not None:
            self.__check_loop()
            if self.sender_task is not None:
                self.sender_task.cancel()
                self.sender_task = None

            if self.receiver_task is not None:
                self.receiver_task.cancel()
                self.receiver_task = None

            if self.stream is not None:
                self.stream.close()
                self.stream = None

            self.events = None
            self.loop = None

    async def async_next_update(self) -> DiscoveredCube:
        self.__check_loop()
        return await self.events.get()

    async def async_get_cube(self, serial: str) -> DiscoveredCube:
        self.__check_loop()
        return self.cubes.get(serial)

    async def async_reboot_cube(self, serial: str):
        if serial == BROADCAST_SERIAL:
            raise ValueError("Reboot comment cannot be broadcasted")
        self.__check_loop()
        msg = Explorer.__cmd(REBOOT_CMD, to=serial)
        await self.stream.send(msg, (self.cubes[serial].address, DISCOVERY_PORT))

    def __check_loop(self):
        if self.loop is None:
            raise RuntimeError("MaxCube discovery not started.")
        if self.loop != asyncio.get_running_loop():
            raise RuntimeError("MaxCube discovery is running in another thread")

    async def __sender(self):
        cmd = Explorer.__cmd(DISCOVERY_CMD, to=BROADCAST_SERIAL)
        while True:
            await self.stream.send(cmd, BROADCAST_ADDRESS)
            await asyncio.sleep(self.broadcast_period)

    async def __receiver(self):
        while True:
            reply, addr = await self.stream.recv()
            print("Response from %s: %s\n" % (addr, reply.hex()))
            cube = Explorer.__process_reply(reply, addr)
            if not cube:
                continue

            print("Cube detected: %s\n" % cube)
            if cube.serial not in self.cubes or cube != self.cubes[cube.serial]:
                self.cubes[cube.serial] = cube
                await self.events.put(cube)

    @staticmethod
    def __cmd(cmd: bytes, to: str) -> bytes:
        return DISCOVERY_HEADER + to.encode('utf-8') + cmd

    @staticmethod
    def __process_reply(reply: bytes, addr: Tuple[str, int]) -> DiscoveredCube:
        if (len(reply) < 26
                or not reply.startswith(DISCOVERY_MAGIC)
                or reply[19:20] != DISCOVERY_CMD):
            return None

        serial = reply[8:18].decode('utf-8')
        rf_address = reply[21:24].hex()
        fw_version = "%d.%d.%d" % (int(reply[24]), int(reply[25] >> 4), int(reply[25] % 16))
        return DiscoveredCube(serial, addr[0], rf_address, fw_version)

    @staticmethod
    async def __open_stream(*, broadcast: bool = False):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1 if broadcast else 0)
            s.bind(BIND_ADDRESS)
            return await asyncio_dgram.from_socket(s)
        except:
            s.close()
            raise
