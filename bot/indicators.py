import time
from datetime import datetime
import pandas as pd

from .context import exchange, TRADE_CONFIG
from .okx import _with_rate_limit_retry
from . import state


def calculate_technical_indicators(df):
    try:
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # EMA 指标
        df['ema_20'] = df['close'].ewm(span=20).mean()
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # RSI 指标
        delta = df['close'].diff()
        # RSI(14)
        gain14 = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss14 = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs14 = gain14 / loss14
        df['rsi_14'] = 100 - (100 / (1 + rs14))
        # RSI(7)
        gain7 = (delta.where(delta > 0, 0)).rolling(7).mean()
        loss7 = (-delta.where(delta < 0, 0)).rolling(7).mean()
        rs7 = gain7 / loss7
        df['rsi_7'] = 100 - (100 / (1 + rs7))
        # 兼容旧字段名
        df['rsi'] = df['rsi_14']

        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # 中间价（近似：高低价均值）
        df['mid_price'] = (df['high'] + df['low']) / 2.0

        df = df.bfill().ffill()
        return df
    except Exception as e:
        print(f"技术指标计算失败: {e}")
        return df


def get_support_resistance_levels(df, lookback=20):
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        # 斐波纳契回撤位（基于近 lookback 区间的 swing 高低）
        try:
            swing_high = float(recent_high)
            swing_low = float(recent_low)
            diff = max(swing_high - swing_low, 1e-9)
            fib_levels = {
                'fib_23_6': swing_high - 0.236 * diff,
                'fib_38_2': swing_high - 0.382 * diff,
                'fib_50': swing_high - 0.5 * diff,
                'fib_61_8': swing_high - 0.618 * diff,
                'fib_78_6': swing_high - 0.786 * diff,
            }
        except Exception:
            fib_levels = None

        # 枢轴位（上一交易日）
        pivots = None
        try:
            if 'timestamp' in df.columns and pd.api.types.is_datetime64_any_dtype(df['timestamp']):
                df_day = df.set_index('timestamp')
                day_ohlc = df_day[['open', 'high', 'low', 'close']].resample('1D').agg({
                    'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
                }).dropna()
                if len(day_ohlc) >= 2:
                    prev = day_ohlc.iloc[-2]
                    h = float(prev['high']); l = float(prev['low']); c = float(prev['close'])
                    pp = (h + l + c) / 3.0
                    pivots = {
                        'pp': pp,
                        'r1': 2 * pp - l,
                        's1': 2 * pp - h,
                        'r2': pp + (h - l),
                        's2': pp - (h - l),
                        'r3': h + 2 * (pp - l),
                        's3': l - 2 * (h - pp)
                    }
        except Exception:
            pivots = None

        return {
            'static_resistance': recent_high,
            'static_support': recent_low,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((recent_high - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - recent_low) / recent_low) * 100,
            'fibonacci': fib_levels,
            'pivots': pivots
        }
    except Exception as e:
        print(f"支撑阻力计算失败: {e}")
        return {}


def get_market_trend(df):
    try:
        current_price = df['close'].iloc[-1]
        trend_short = "上涨" if current_price > df['sma_20'].iloc[-1] else "下跌"
        trend_medium = "上涨" if current_price > df['sma_50'].iloc[-1] else "下跌"
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"
        if trend_short == "上涨" and trend_medium == "上涨":
            overall_trend = "强势上涨"
        elif trend_short == "下跌" and trend_medium == "下跌":
            overall_trend = "强势下跌"
        else:
            overall_trend = "震荡整理"
        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1]
        }
    except Exception as e:
        print(f"趋势分析失败: {e}")
        return {}


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()
        return atr
    except Exception:
        return pd.Series([None] * len(df), index=df.index)


def get_btc_ohlcv_enhanced():
    try:
        now_ts = time.time()
        if (state.last_price_data_cache is not None) and (now_ts - state.last_analysis_ts < float(state.ANALYSIS_UPDATE_INTERVAL or 0)):
            try:
                ohlcv_latest = _with_rate_limit_retry(lambda: exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'], limit=1))
                if ohlcv_latest and len(ohlcv_latest) > 0:
                    state.last_price_data_cache['price'] = ohlcv_latest[0][4]
                    state.last_price_data_cache['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pass
            return state.last_price_data_cache

        ohlcv = _with_rate_limit_retry(
            lambda: exchange.fetch_ohlcv(
                TRADE_CONFIG['symbol'],
                TRADE_CONFIG['timeframe'],
                limit=TRADE_CONFIG['data_points']
            )
        )

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        df = calculate_technical_indicators(df)

        trend_4h = None
        boll_4h = None
        levels_4h = None
        background_4h = None
        try:
            ohlcv_4h = _with_rate_limit_retry(lambda: exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '4h', limit=120))
            df4 = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df4['timestamp'] = pd.to_datetime(df4['timestamp'], unit='ms')
            # 4H 布林带
            df4['bb_middle'] = df4['close'].rolling(20).mean()
            bb4_std = df4['close'].rolling(20).std()
            df4['bb_upper'] = df4['bb_middle'] + (bb4_std * 2)
            df4['bb_lower'] = df4['bb_middle'] - (bb4_std * 2)
            # 4H EMA、MACD、RSI、ATR、成交量均值
            df4['ema_20'] = df4['close'].ewm(span=20).mean()
            df4['ema_50'] = df4['close'].ewm(span=50).mean()
            df4['ema_12'] = df4['close'].ewm(span=12).mean()
            df4['ema_26'] = df4['close'].ewm(span=26).mean()
            df4['macd'] = df4['ema_12'] - df4['ema_26']
            df4['macd_signal'] = df4['macd'].ewm(span=9).mean()
            d4_delta = df4['close'].diff()
            gain14_4h = (d4_delta.where(d4_delta > 0, 0)).rolling(14).mean()
            loss14_4h = (-d4_delta.where(d4_delta < 0, 0)).rolling(14).mean()
            rs14_4h = gain14_4h / loss14_4h
            df4['rsi_14'] = 100 - (100 / (1 + rs14_4h))
            atr3_4h = _compute_atr(df4, period=3)
            atr14_4h = _compute_atr(df4, period=14)
            vol_avg_4h = df4['volume'].rolling(20, min_periods=1).mean()
            df4 = df4.bfill().ffill()
            last4 = df4.iloc[-1]
            price4 = float(last4['close'])
            upper4 = float(last4['bb_upper'])
            lower4 = float(last4['bb_lower'])
            middle4 = float(last4['bb_middle'])
            if price4 > middle4:
                overall4 = '上涨'
            elif price4 < middle4:
                overall4 = '下跌'
            else:
                overall4 = '震荡'
            pos4 = (price4 - lower4) / max((upper4 - lower4), 1e-9)
            trend_4h = {
                'overall': overall4,
                'bb_position': pos4,
                'price': price4
            }
            boll_4h = {
                'bb_upper': upper4,
                'bb_middle': middle4,
                'bb_lower': lower4
            }
            # 使用4H数据计算关键位（含斐波回撤）
            levels_4h = get_support_resistance_levels(df4)
            # 4H 背景汇总（用于 Prompt 长期框架）
            macd_series_4h = df4['macd'].tail(10).tolist()
            rsi_series_4h = df4['rsi_14'].tail(10).tolist()
            background_4h = {
                'ema20': float(last4['ema_20']),
                'ema50': float(last4['ema_50']),
                'atr3': float(atr3_4h.iloc[-1]) if len(atr3_4h) else None,
                'atr14': float(atr14_4h.iloc[-1]) if len(atr14_4h) else None,
                'volume_current': float(last4['volume']),
                'volume_avg': float(vol_avg_4h.iloc[-1]) if len(vol_avg_4h) else None,
                'macd_series': macd_series_4h,
                'rsi14_series': rsi_series_4h,
            }
        except Exception:
            trend_4h = None
            boll_4h = None
            levels_4h = None
            background_4h = None

        levels_15m = None
        kline_15m_data = None
        try:
            ohlcv_15m = _with_rate_limit_retry(lambda: exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], '15m', limit=96))
            df15 = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df15['timestamp'] = pd.to_datetime(df15['timestamp'], unit='ms')
            df15['bb_middle'] = df15['close'].rolling(20).mean()
            bb15_std = df15['close'].rolling(20).std()
            df15['bb_upper'] = df15['bb_middle'] + (bb15_std * 2)
            df15['bb_lower'] = df15['bb_middle'] - (bb15_std * 2)
            df15 = df15.bfill().ffill()
            recent_high_15 = df15['high'].tail(20).max()
            recent_low_15 = df15['low'].tail(20).min()
            last15 = df15.iloc[-1]
            levels_15m = {
                'static_resistance': float(recent_high_15),
                'static_support': float(recent_low_15),
                'bb_upper': float(last15['bb_upper']),
                'bb_middle': float(last15['bb_middle']),
                'bb_lower': float(last15['bb_lower'])
            }
            kline_15m_data = df15[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records')
        except Exception:
            levels_15m = None
            kline_15m_data = None

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        trend_analysis = get_market_trend(df)
        # 统一使用4H的斐波回撤与关键位；若4H获取失败，则回退到当前周期
        levels_analysis = levels_4h if levels_4h else get_support_resistance_levels(df)

        try:
            symbol = TRADE_CONFIG.get('symbol', 'BTC/USDT:USDT')
            base = symbol.split('/')[0]
        except Exception:
            symbol = 'BTC/USDT:USDT'
            base = 'BTC'

        result = {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG['timeframe'],
            'symbol': symbol,
            'base': base,
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'ema_20': current_data.get('ema_20', 0),
                'rsi': current_data.get('rsi', 0),
                'rsi_7': current_data.get('rsi_7', 0),
                'rsi_14': current_data.get('rsi_14', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'trend_4h': trend_4h,
            'boll_4h': boll_4h,
            'levels_4h': levels_4h,
            'levels_15m': levels_15m,
            'kline_15m_data': kline_15m_data,
            # 日内序列（默认按当前 timeframe 的最近10条）
            'intraday_series': {
                'mid_prices': df['mid_price'].tail(10).tolist() if 'mid_price' in df.columns else None,
                'ema20': df['ema_20'].tail(10).tolist() if 'ema_20' in df.columns else None,
                'macd': df['macd'].tail(10).tolist() if 'macd' in df.columns else None,
                'rsi7': df['rsi_7'].tail(10).tolist() if 'rsi_7' in df.columns else None,
                'rsi14': df['rsi_14'].tail(10).tolist() if 'rsi_14' in df.columns else None,
            },
            # 4H 背景框架摘要
            'background_4h': background_4h,
            'full_data': df
        }

        state.last_price_data_cache = result
        state.last_analysis_ts = now_ts
        return result
    except Exception as e:
        print(f"获取增强K线数据失败: {e}")
        return None


