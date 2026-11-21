import asyncio
import pprint

from schwab.streaming import StreamClient
import schwab

API_KEY = "XXXXXX"
CLIENT_SECRET = "XXXXXX"
CALLBACK_URL = "https://xxxxxx"

class MyStreamConsumer:
    """
    We use a class to enforce good code organization practices
    """

    def __init__(self, api_key, client_secret, callback_url, queue_size=100,
                 token_path='./token.json'):
        """
        We're storing the configuration variables within the class for easy
        access later in the code!
        """
        self.api_key = api_key
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.token_path = token_path

        self.schwab_client = None
        self.stream_client = None

        self.symbols = [
            'GOOG', 'GOOGL', 'BP', 'CVS', 'ADBE', 'CRM', 'SNAP', 'AMZN',
            'BABA', 'DIS', 'M', 'USO', 'AAPL', 'NFLX', 'GE', 'TSLA',
            'F', 'SPY', 'FDX', 'UBER', 'ROKU', 'META', 'BIDU'
        ]

        # Create a queue so we can queue up work gathered from the client
        self.queue = asyncio.Queue(queue_size)

    def initialize(self):
        """
        Create the clients and log in. Token should be previously generated using client_from_manual_flow()

        TODO: update to easy_client() when client_from_login_flow() works, 
        or when easy_client() can redirect to client_from_manual_flow()
        """
        self.schwab_client = schwab.auth.client_from_token_file(self.token_path, 
            api_key=self.api_key,
            app_secret=self.client_secret)

        self.stream_client = StreamClient(self.schwab_client)

        # The streaming client wants you to add a handler for every service type
        self.stream_client.add_level_one_equity_handler(
            self.handle_level_one_equity)

    async def stream(self):
        await self.stream_client.login()  # Log into the streaming service

        await self.stream_client.level_one_equity_subs(self.symbols)

        # Propagate output errors even if dispatch is waiting for queue space.
        async with asyncio.TaskGroup() as workers:
            workers.create_task(self.handle_queue())
            while True:
                await self.stream_client.handle_message()

    async def handle_level_one_equity(self, msg):
        """Wait for queue capacity so partial quote updates retain FIFO order."""
        await self.queue.put(msg)

    async def handle_queue(self):
        """
        Here we pull messages off the queue and process them.
        """
        while True:
            msg = await self.queue.get()
            try:
                await asyncio.to_thread(pprint.pprint, msg)
            finally:
                self.queue.task_done()


async def main():
    """
    Create and instantiate the consumer, and start the stream
    """
    consumer = MyStreamConsumer(api_key=API_KEY, client_secret=CLIENT_SECRET, callback_url=CALLBACK_URL)

    consumer.initialize()
    await consumer.stream()

if __name__ == '__main__':
    asyncio.run(main())
