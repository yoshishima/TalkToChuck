import json
from schwab.streaming import StreamJsonDecoder


class HeuristicJsonDecoder(StreamJsonDecoder):
    def decode_json_string(self, raw):
        '''
        Attempts the following, in order:
        
        1. Return the JSON decoding of the raw string.
        2. Replace all instances of ``\\\\\\\\`` with ``\\\\`` and return the
           decoding.

        Note alternative (and potentially expensive) transformations are only
        performed when ``JSONDecodeError`` exceptions are raised by earlier
        stages. The replacement applies to the whole frame, so it can also
        change string values that legitimately contain escaped backslashes.
        Binary frames are decoded as UTF-8 before the replacement.
        '''
        try:
            return json.loads(raw)
        except json.decoder.JSONDecodeError:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode('utf-8')
            raw = raw.replace('\\\\', '\\')

        return json.loads(raw)
