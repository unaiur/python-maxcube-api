#! /usr/bin/env python3

import asyncio

from maxcube import discover

async def main():
    await discover.async_start()
    while True:
        cube = await discover.async_next_update()
        print("Cube updated: %s" % (cube))
        # await explorer.async_reboot_cube(cube.serial)

# Python 3.7+
asyncio.run(main())
