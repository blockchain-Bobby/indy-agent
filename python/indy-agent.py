""" indy-agent python implementation
"""

# Pylint struggles to find packages inside of a virtual environments;
# pylint: disable=import-error

# Pylint also dislikes the name indy-agent but this follows conventions already
# established in indy projects.
# pylint: disable=invalid-name

import asyncio
import sys
import uuid
import aiohttp_jinja2
import jinja2
import base64
import json
import argparse

from aiohttp import web
from indy import crypto, did, error, IndyError, wallet

from helpers import deserialize_bytes_json, str_to_bytes, bytes_to_str
from modules.connection import Connection, AdminConnection
from modules.admin import Admin
from modules.admin_walletconnection import AdminWalletConnection
from modules.basicmessage import AdminBasicMessage, BasicMessage

import modules.admin
import serializer.json_serializer as Serializer
from receiver.message_receiver import MessageReceiver as Receiver
from websocket_handler import WebSocketHandler
from agent import Agent
from message import Message


# Argument Parsing
parser = argparse.ArgumentParser()
parser.add_argument("port", nargs="?", default="8080", type=int, help="The port to attach.")
parser.add_argument("--wallet", nargs=2, metavar=('walletname','walletpass'), help="The name and passphrase of the wallet to connect to.")
parser.add_argument("--ephemeralwallet", action="store_true", help="Use ephemeral wallets")
args = parser.parse_args()

# config webapp

LOOP = asyncio.get_event_loop()

WEBAPP = web.Application()

aiohttp_jinja2.setup(WEBAPP, loader=jinja2.FileSystemLoader('view'))


AGENT = Agent()
POST_MESSAGE_RECEIVER = Receiver(AGENT.message_queue)
ADMIN_MESSAGE_HANDLER = WebSocketHandler(AGENT.message_queue, AGENT.outbound_admin_message_queue)


WEBAPP['agent'] = AGENT

AGENT.register_module(Admin)
AGENT.register_module(Connection)
AGENT.register_module(AdminConnection)
AGENT.register_module(AdminWalletConnection)
AGENT.register_module(BasicMessage)
AGENT.register_module(AdminBasicMessage)


ROUTES = [
    web.get('/', modules.admin.root),
    web.get('/ws', ADMIN_MESSAGE_HANDLER.ws_handler),
    web.static('/res', 'view/res'),
    web.post('/indy', POST_MESSAGE_RECEIVER.handle_message),
]

WEBAPP.add_routes(ROUTES)

RUNNER = web.AppRunner(WEBAPP)
LOOP.run_until_complete(RUNNER.setup())

SERVER = web.TCPSite(runner=RUNNER, port=args.port)

if args.wallet:
    try:
        LOOP.run_until_complete(AGENT.connect_wallet(args.wallet[0], args.wallet[1], ephemeral=args.ephemeralwallet))
        print("Connected to wallet via command line args: {}".format(args.wallet[0]))
    except Exception as e:
        print(e)
else:
    print("Configure wallet connection via UI.")

async def message_process(agent):
    """ Message processing loop task.
    """
    while True:
        wire_msg_bytes = await AGENT.message_queue.get()

        # Try to unpack message assuming it's not encrypted
        try:
            msg = Serializer.unpack(wire_msg_bytes)
        except Exception as e:
            print("Message encryped, attempting to unpack...")

        # TODO: More graceful checking here
        if not isinstance(msg, Message) or "@type" not in msg:
            # Message IS encrypted so unpack it
            try:
                msg = await agent['agent'].unpack_agent_message(wire_msg_bytes)
            except Exception as e:
                print('Failed to unpack message: {}\n\nError: {}'.format(wire_msg_bytes, e))
                continue  # handle next message in loop

        #route message through agent class
        res = await AGENT.route_message_to_module(msg)

        if res is not None:
            await AGENT.send_admin_message(res)

try:
    print('===== Starting Server on: http://localhost:{} ====='.format(args.port))
    LOOP.create_task(SERVER.start())
    LOOP.create_task(message_process(WEBAPP))
    LOOP.run_forever()
except KeyboardInterrupt:
    print("exiting")
