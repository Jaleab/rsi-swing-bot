import pandas as pd
from typing import Tuple

def calculate_rsi(df: pd.DataFrame, column: str = 'close', length: int = 14) -> pd.Series:
    """
    Calculate the Relative Strength Index (RSI) for a given DataFrame.

    Args:
        df (pd.DataFrame): DataFrame with 'close' prices.
        column (str): The name of the column to calculate RSI on.
        length (int): The RSI period.

    Returns:
        pd.Series: A Series containing the RSI values.
    """
    delta = df[column].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_rsi_signal(
    rsi_value: float,
    rsi_oversold: float,
    rsi_overbought: float
) -> Tuple[str, float]:
    """
    Determines an RSI signal based on overbought/oversold thresholds.

    Args:
        rsi_value (float): The current RSI value.
        rsi_oversold (float): The oversold threshold.
        rsi_overbought (float): The overbought threshold.

    Returns:
        Tuple[str, float]: A tuple containing the signal type ("LONG", "SHORT", "NEUTRAL")
                           and a confidence score (0.0 to 1.0).
    """
    if rsi_value < rsi_oversold:
        # The further below oversold, the stronger the signal
        confidence = (rsi_oversold - rsi_value) / rsi_oversold
        return "LONG", min(1.0, confidence * 2) # Multiply to make it stronger faster
    elif rsi_value > rsi_overbought:
        # The further above overbought, the stronger the signal
        confidence = (rsi_value - rsi_overbought) / (100 - rsi_overbought)
        return "SHORT", min(1.0, confidence * 2)
    else:
        return "NEUTRAL", 0.0

if __name__ == "__main__":
    # Example Usage:
    # Create a dummy DataFrame for testing
    data = {
        'open': [10, 12, 15, 13, 11, 14, 16, 18, 17, 19, 20, 22, 21, 23, 25, 24, 26, 28, 27, 29, 30],
        'high': [13, 16, 16, 14, 15, 17, 18, 19, 20, 21, 23, 24, 23, 25, 27, 26, 28, 29, 28, 30, 31],
        'low': [9, 11, 12, 11, 10, 13, 15, 16, 16, 18, 19, 20, 19, 21, 23, 22, 24, 26, 25, 27, 28],
        'close': [12, 15, 13, 11, 14, 16, 18, 17, 19, 20, 22, 21, 23, 25, 24, 26, 28, 27, 29, 30, 32]
    }
    df = pd.DataFrame(data)

    # Calculate RSI with default length 14
    df['rsi'] = calculate_rsi(df, column='close', length=14)
    print("DataFrame with RSI:")
    print(df)

    # Get RSI signals
    rsi_oversold_threshold = 30
    rsi_overbought_threshold = 70

    print("\nRSI Signals:")
    for index, row in df.iterrows():
        if pd.notna(row['rsi']):
            signal, confidence = get_rsi_signal(row['rsi'], rsi_oversold_threshold, rsi_overbought_threshold)
            print(f"Index: {index}, RSI: {row['rsi']:.2f}, Signal: {signal}, Confidence: {confidence:.2f}")
