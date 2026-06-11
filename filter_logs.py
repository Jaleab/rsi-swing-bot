import re
import sys

def filter_timestamps(log_file_path, output_file_path):
    with open(log_file_path, 'r') as infile, open(output_file_path, 'w') as outfile:
        for line in infile:
            # Regex to match and remove timestamps from Docker compose logs and Python logs
            # Example: time="2025-12-18T17:38:21-06:00"
            # Example: 2025-12-18 23:38:17,740
            cleaned_line = re.sub(r'time="[^"]+" level=warning msg="', 'level=warning msg="', line)
            cleaned_line = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} -', '', cleaned_line)
            
            # Also remove the "trading_bot | " prefix which includes container-specific log metadata
            cleaned_line = re.sub(r'trading_bot\s*\|\s*', '', cleaned_line)
            
            # Remove any remaining timestamps like "2025-12-19 05:16:41,033" which might appear in the middle of a message
            cleaned_line = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}', '', cleaned_line)

            # Remove Python object memory addresses (e.g., <src.ws_liquidation.LiquidationEvent object at 0x...>)
            cleaned_line = re.sub(r' object at 0x[0-9a-fA-F]+>', ' object at 0xADDRESS>', cleaned_line)

            # Remove event_queue id (e.g., event_queue id: 132724390296160)
            cleaned_line = re.sub(r'event_queue id: \d+', 'event_queue id: ID', cleaned_line)

            # Remove timestamp from TradeEvent and OrderBookEvent objects (e.g., timestamp=1766199132496)
            cleaned_line = re.sub(r'timestamp=\d+', 'timestamp=TIMESTAMP', cleaned_line)

            # Remove trade_id from TradeEvent objects (e.g., trade_id='synthetic_trade_1766199132496_BTC/USDT')
            cleaned_line = re.sub(r"trade_id='synthetic_trade_\d+_[^']+'", "trade_id='synthetic_trade_ID'", cleaned_line)

            # Remove "Last Update" timestamp from SimpleMonitor output
            cleaned_line = re.sub(r'Last Update: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC', 'Last Update: TIMESTAMP', cleaned_line)
            
            outfile.write(cleaned_line)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_logs.py <input_log_file> <output_log_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    filter_timestamps(input_file, output_file)