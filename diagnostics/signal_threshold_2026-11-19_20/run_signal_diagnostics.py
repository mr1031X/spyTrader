from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend import strategies as strategy_module
from backend.config import BotSettings
from backend.database import SessionLocal, get_settings
from backend.indicators import atr, closed_candles, compute_indicators, filter_regular_session, resample_candles, session_vwap
from backend.signal_engine import SignalEngine
from backend.strategies import (
    SignalCandidate, StrategyEngine, StrategyEvaluation, _momentum, _trend, _volatility_score,
)

OUT = Path(__file__).resolve().parent
DATA = Path('/tmp/spytrader-underlying-2026-11-19_20.csv')
ET = 'America/New_York'
MAIN_DAYS = {'2026-11-19', '2026-11-20'}
TECHNICAL_WEIGHT = 80.0


def frame_key(frame: pd.DataFrame) -> tuple:
    if frame.empty:
        return (0, None, None)
    return (len(frame), str(frame['timestamp'].iloc[0]), str(frame['timestamp'].iloc[-1]))


def build_contexts(data: pd.DataFrame, settings: BotSettings,
                   timeline_start: datetime | None = None,
                   timeline_end: datetime | None = None) -> list[dict[str, Any]]:
    data = data.copy()
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True)
    data = data.sort_values('timestamp').reset_index(drop=True)
    rows_by_symbol = {symbol: rows.reset_index(drop=True) for symbol, rows in data.groupby('symbol')}
    timeline = sorted(data['timestamp'].unique())
    if timeline_start is not None:
        timeline = [timestamp for timestamp in timeline if pd.Timestamp(timestamp) >= pd.Timestamp(timeline_start)]
    if timeline_end is not None:
        timeline = [timestamp for timestamp in timeline if pd.Timestamp(timestamp) <= pd.Timestamp(timeline_end)]
    engine = StrategyEngine()
    original_compute = strategy_module.compute_indicators
    original_vwap_at = strategy_module.session_vwap_at
    contexts = []
    try:
        for timestamp in timeline:
            ts = pd.Timestamp(timestamp)
            for symbol, rows in rows_by_symbol.items():
                end = rows['timestamp'].searchsorted(ts, side='right')
                visible = rows.iloc[:end].tail(1600)
                one = filter_regular_session(closed_candles(visible, now=ts.to_pydatetime()))
                five = resample_candles(one, 5, now=ts.to_pydatetime())
                fifteen = resample_candles(one, 15, now=ts.to_pydatetime())
                warmed = len(one) >= 60 and len(five) >= 55 and len(fifteen) >= 55
                if not warmed:
                    evaluations = [StrategyEvaluation(name, rejected=['INSUFFICIENT_HISTORICAL_DATA']) for name in settings.enabled_strategies]
                    contexts.append({'ts': ts, 'symbol': symbol, 'evaluations': evaluations, 'warmed': False})
                    continue
                i1 = compute_indicators(one, settings.opening_range_minutes)
                i5 = compute_indicators(five, settings.opening_range_minutes)
                i15 = compute_indicators(fifteen, settings.opening_range_minutes)
                previous_vwap = float(session_vwap(one).iloc[-2])
                cache = {frame_key(one): i1, frame_key(five): i5, frame_key(fifteen): i15}
                one_key = frame_key(one)

                def cached_compute(frame: pd.DataFrame, opening_range_minutes: int = 15):
                    return cache.get(frame_key(frame)) or original_compute(frame, opening_range_minutes)

                def cached_vwap_at(frame: pd.DataFrame, index: int):
                    if frame_key(frame) == one_key and index == -2:
                        return previous_vwap
                    return original_vwap_at(frame, index)

                strategy_module.compute_indicators = cached_compute
                strategy_module.session_vwap_at = cached_vwap_at
                evaluations = engine.evaluate_detailed(
                    symbol, one, five, fifteen, settings.enabled_strategies,
                    settings.opening_range_minutes,
                )
                contexts.append({
                    'ts': ts, 'symbol': symbol, 'evaluations': evaluations, 'warmed': True,
                    'i1': i1, 'i5': i5, 'i15': i15,
                    'current': one.iloc[-1][['open', 'high', 'low', 'close', 'volume']].to_dict(),
                    'previous': one.iloc[-2][['open', 'high', 'low', 'close', 'volume']].to_dict(),
                    'recent': five.iloc[-6:-1][['open', 'high', 'low', 'close', 'volume']].to_dict('records'),
                    'previous_vwap': previous_vwap,
                })
    finally:
        strategy_module.compute_indicators = original_compute
        strategy_module.session_vwap_at = original_vwap_at
    return contexts


def regime_candidate(evaluation: StrategyEvaluation, candidate: SignalCandidate | None, ctx: dict) -> SignalCandidate | None:
    if candidate is None:
        return None
    trend = str(ctx['i15'].trend) if hasattr(ctx['i15'], 'trend') else ''
    from backend.strategies import classify_trend, classify_volatility
    trend = classify_trend(ctx['i15'])
    volatility = classify_volatility(ctx['i15'])
    evaluation.trend = trend
    evaluation.volatility = volatility
    incompatible = (
        volatility == 'extreme'
        or (candidate.strategy == 'trend_continuation' and trend == 'sideways')
        or (candidate.strategy == 'momentum_breakout' and volatility == 'low')
    )
    if incompatible:
        evaluation.rejected.append('UNSTABLE_MARKET' if volatility == 'extreme' else 'REGIME_MISMATCH')
        return None
    return SignalCandidate(**{**candidate.__dict__, 'regime': trend, 'trend': trend, 'volatility': volatility})


def experimental_evaluation(ctx: dict, name: str, config: dict) -> StrategyEvaluation:
    if not ctx['warmed']:
        return StrategyEvaluation(name, rejected=['INSUFFICIENT_HISTORICAL_DATA'])
    i1, i5 = ctx['i1'], ctx['i5']
    current, previous = ctx['current'], ctx['previous']
    evaluation = StrategyEvaluation(name)
    volume_override = config.get('volume')
    extension_override = config.get('extension')
    vwap_mandatory = config.get('vwap_mandatory', True)
    ema_mandatory = config.get('ema_mandatory', True)
    candle_mandatory = config.get('candle_mandatory', True)

    def require(condition: bool, code: str, mandatory: bool = True):
        if not condition and mandatory:
            evaluation.rejected.append(code)

    if name == 'momentum_breakout':
        fresh = config.get('fresh_cross', True)
        direction = None
        if current['close'] > i1.opening_range_high and (previous['close'] <= i1.opening_range_high or not fresh):
            direction = 'call'
        elif current['close'] < i1.opening_range_low and (previous['close'] >= i1.opening_range_low or not fresh):
            direction = 'put'
        evaluation.direction = direction
        evaluation.conditions['fresh_opening_range_cross'] = direction is not None
        volume_ok = i1.relative_volume >= (volume_override if volume_override is not None else 1.2)
        evaluation.conditions['relative_volume'] = volume_ok
        require(direction is not None, 'BREAKOUT_NOT_CONFIRMED')
        require(volume_ok, 'INSUFFICIENT_VOLUME')
        if direction:
            ema = i5.ema9 > i5.ema21 > i5.ema50 if direction == 'call' else i5.ema9 < i5.ema21 < i5.ema50
            vwap = i1.price > i1.vwap if direction == 'call' else i1.price < i1.vwap
            extension_limit = extension_override if extension_override is not None else 2.5
            extension = abs(i1.price - i1.vwap) / i1.atr <= extension_limit
            candle = (current['close'] > current['open'] and previous['close'] >= previous['open']) if direction == 'call' else (
                current['close'] < current['open'] and previous['close'] <= previous['open'])
            expansion = i1.volatility_ratio >= 0.75
            evaluation.conditions.update({'ema_alignment': ema, 'vwap_alignment': vwap, 'not_overextended': extension,
                                          'candle_confirmation': candle, 'atr_expansion': expansion})
            require(ema, 'EMA_MISALIGNMENT', ema_mandatory)
            require(vwap, 'VWAP_MISALIGNMENT', vwap_mandatory)
            require(extension, 'OVEREXTENDED_PRICE')
            require(candle, 'BREAKOUT_NOT_CONFIRMED', candle_mandatory)
            require(expansion, 'INSUFFICIENT_ATR_EXPANSION')
        if evaluation.rejected or direction is None:
            return evaluation
        candidate = SignalCandidate(
            ctx['symbol'], direction, name, '', {
                'momentum': _momentum(i1, direction), 'trend': _trend(i5, direction),
                'volume': min(i1.relative_volume / 2.5, 1.0), 'volatility': _volatility_score(i1, 1.2),
            }, ['Diagnostic experimental candidate'], i1.price, i1.atr,
            event_key=f'opening-range:{ctx["ts"].isoformat()}',
        )
        evaluation.candidate = regime_candidate(evaluation, candidate, ctx)
        return evaluation

    if name == 'trend_continuation':
        direction = None
        ema = False
        if i5.ema9 > i5.ema21 > i5.ema50:
            direction, ema = 'call', True
        elif i5.ema9 < i5.ema21 < i5.ema50:
            direction, ema = 'put', True
        elif not ema_mandatory:
            if i1.macd_histogram > 0 and (i1.price > i1.vwap or not vwap_mandatory):
                direction = 'call'
            elif i1.macd_histogram < 0 and (i1.price < i1.vwap or not vwap_mandatory):
                direction = 'put'
        evaluation.direction = direction
        evaluation.conditions['ema_alignment'] = ema
        require(direction is not None, 'EMA_MISALIGNMENT', ema_mandatory)
        if direction is None:
            evaluation.rejected.append('REGIME_MISMATCH')
            return evaluation
        recent = pd.DataFrame(ctx['recent'])
        bullish = direction == 'call'
        vwap = i1.price > i1.vwap if bullish else i1.price < i1.vwap
        structure = ((recent['high'].iloc[-1] > recent['high'].iloc[0] and recent['low'].iloc[-1] > recent['low'].iloc[0])
                     if bullish else (recent['high'].iloc[-1] < recent['high'].iloc[0] and recent['low'].iloc[-1] < recent['low'].iloc[0]))
        renewed = ((current['close'] > previous['high'] and i1.macd_histogram > 0) if bullish else
                   (current['close'] < previous['low'] and i1.macd_histogram < 0))
        dynamic = abs(i1.price - i1.ema9) <= i1.atr * 0.8 or abs(i1.price - i1.vwap) <= i1.atr * 0.6
        beyond = ((current['close'] > i1.opening_range_high and recent['high'].max() > i1.opening_range_high) if bullish else
                  (current['close'] < i1.opening_range_low and recent['low'].min() < i1.opening_range_low))
        retest = ((previous['low'] <= i1.opening_range_high + i1.atr * 0.3 and current['close'] > i1.opening_range_high) if bullish else
                  (previous['high'] >= i1.opening_range_low - i1.atr * 0.3 and current['close'] < i1.opening_range_low))
        consolidation_range = float(recent['high'].iloc[-4:].max() - recent['low'].iloc[-4:].min())
        consolidation = consolidation_range <= i5.atr * 1.8 and (
            current['close'] > recent['high'].iloc[-4:].max() if bullish else current['close'] < recent['low'].iloc[-4:].min())
        structure_break = current['close'] > recent['high'].max() if bullish else current['close'] < recent['low'].min()
        volume_ok = i1.relative_volume >= (volume_override if volume_override is not None else 0.8)
        extension_limit = extension_override if extension_override is not None else 2.5
        extension = abs(i1.price - i1.vwap) / i1.atr <= extension_limit
        trigger = renewed or consolidation or structure_break
        location = dynamic or beyond or retest or structure_break
        evaluation.conditions.update({
            'vwap_alignment': vwap, 'directional_structure': structure, 'renewed_momentum': renewed,
            'pullback_or_dynamic_support': dynamic, 'confirmed_breakout': beyond, 'failed_retest': retest,
            'consolidation_continuation': consolidation, 'subsequent_structure_break': structure_break,
            'relative_volume': volume_ok, 'not_overextended': extension,
            'continuation_trigger': trigger, 'continuation_location': location,
        })
        require(vwap, 'VWAP_MISALIGNMENT', vwap_mandatory)
        require(structure, 'TREND_STRUCTURE_MISSING')
        require(volume_ok, 'INSUFFICIENT_VOLUME')
        require(extension, 'OVEREXTENDED_PRICE')
        require(trigger, 'CONTINUATION_NOT_CONFIRMED')
        require(location, 'NO_PULLBACK_OR_FAILED_RETEST')
        if evaluation.rejected:
            return evaluation
        setup = 'consolidation' if consolidation else ('failed-retest' if retest else ('pullback' if dynamic else 'structure-break'))
        candidate = SignalCandidate(
            ctx['symbol'], direction, name, '', {
                'momentum': _momentum(i1, direction), 'trend': _trend(i5, direction),
                'volume': min(i1.relative_volume / 2.0, 1.0), 'volatility': _volatility_score(i1, 1.0),
            }, ['Diagnostic experimental candidate'], i1.price, i1.atr,
            event_key=f'{setup}:{ctx["ts"].isoformat()}',
        )
        evaluation.candidate = regime_candidate(evaluation, candidate, ctx)
        return evaluation

    previous_close = float(previous['close'])
    previous_vwap = ctx['previous_vwap']
    direction = None
    if previous_close < previous_vwap and i1.price > i1.vwap:
        direction = 'call'
    elif previous_close > previous_vwap and i1.price < i1.vwap:
        direction = 'put'
    evaluation.direction = direction
    evaluation.conditions['vwap_cross'] = direction is not None
    require(direction is not None, 'VWAP_MISALIGNMENT')
    if direction is None:
        return evaluation
    momentum = (i1.macd_histogram > 0 and i1.rsi > 50) if direction == 'call' else (i1.macd_histogram < 0 and i1.rsi < 50)
    failed = abs(previous_close - previous_vwap) <= i1.atr * 0.5
    volume_ok = i1.relative_volume >= (volume_override if volume_override is not None else 1.3)
    extension_limit = extension_override if extension_override is not None else 2.0
    extension = abs(i1.price - i1.vwap) / i1.atr <= extension_limit
    evaluation.conditions.update({'momentum_reversal': momentum, 'failed_breakout': failed,
                                  'relative_volume': volume_ok, 'not_overextended': extension})
    require(momentum, 'MOMENTUM_NOT_CONFIRMED')
    require(failed, 'FAILED_RETEST_NOT_CONFIRMED')
    require(volume_ok, 'INSUFFICIENT_VOLUME')
    require(extension, 'OVEREXTENDED_PRICE')
    if evaluation.rejected:
        return evaluation
    candidate = SignalCandidate(
        ctx['symbol'], direction, name, '', {
            'momentum': _momentum(i1, direction), 'trend': _trend(i5, direction),
            'volume': min(i1.relative_volume / 2.5, 1.0), 'volatility': _volatility_score(i1, 1.3),
        }, ['Diagnostic experimental candidate'], i1.price, i1.atr, stronger_confirmation_required=True,
        event_key=f'vwap-reversal:{ctx["ts"].isoformat()}',
    )
    evaluation.candidate = regime_candidate(evaluation, candidate, ctx)
    return evaluation


def observation(ctx: dict, candidate: SignalCandidate, variant: str, settings: BotSettings) -> dict[str, Any]:
    scored = SignalEngine().score_technical(candidate, settings)
    local = ctx['ts'].tz_convert(ET)
    stronger_ok = not candidate.stronger_confirmation_required or (
        scored.factor_scores['momentum'] >= 0.7 and scored.factor_scores['volume'] >= 0.65)
    return {
        'variant': variant, 'timestamp_utc': ctx['ts'].isoformat(), 'timestamp_et': local.isoformat(),
        'session': local.date().isoformat(), 'time_et': local.strftime('%H:%M'),
        'time_bucket': time_bucket(local), 'underlying': ctx['symbol'], 'strategy': candidate.strategy,
        'direction': candidate.direction, 'trend': candidate.trend, 'volatility': candidate.volatility,
        'technical_score': scored.score, 'raw_technical_points': scored.technical_score,
        'stronger_confirmation_passed': stronger_ok, 'event_key': candidate.event_key,
    }


def time_bucket(timestamp) -> str:
    minutes = timestamp.hour * 60 + timestamp.minute
    if minutes < 600:
        return '09:30-10:00'
    if minutes < 660:
        return '10:00-11:00'
    if minutes < 780:
        return '11:00-13:00'
    if minutes < 900:
        return '13:00-15:00'
    return '15:00-close'


def run_variant(contexts: list[dict], name: str, config: dict, settings: BotSettings) -> tuple[list[dict], list[dict]]:
    observations = []
    evaluations = []
    for ctx in contexts:
        for strategy in settings.enabled_strategies:
            evaluation = experimental_evaluation(ctx, strategy, config)
            evaluations.append((ctx, evaluation))
            if evaluation.candidate is not None:
                observations.append(observation(ctx, evaluation.candidate, name, settings))
    return observations, evaluations


def candidate_counts(items: list[dict]) -> dict:
    return {
        'raw_candidates': len(items),
        'bullish': sum(item['direction'] == 'call' for item in items),
        'bearish': sum(item['direction'] == 'put' for item in items),
        'momentum_breakout': sum(item['strategy'] == 'momentum_breakout' for item in items),
        'trend_continuation': sum(item['strategy'] == 'trend_continuation' for item in items),
        'vwap_reversal': sum(item['strategy'] == 'vwap_reversal' for item in items),
        'technical_80_and_confirmation': sum(item['technical_score'] >= 80 and item['stronger_confirmation_passed'] for item in items),
        'unique_15m_events': len({(item['underlying'], item['strategy'], item['direction'],
                                   item['event_key'].split(':')[0], item['timestamp_et'][:16][:15] + str(int(item['timestamp_et'][14:16]) // 15 * 15))
                                  for item in items}),
    }


def condition_rows(main_evaluations: list[tuple[dict, StrategyEvaluation]]) -> tuple[list[dict], list[dict]]:
    conditions = []
    rejections = []
    for strategy in ('momentum_breakout', 'trend_continuation', 'vwap_reversal'):
        selected = [(ctx, evaluation) for ctx, evaluation in main_evaluations if evaluation.strategy == strategy]
        names = sorted({name for _, evaluation in selected for name in evaluation.conditions})
        for name in names:
            values = [bool(evaluation.conditions[name]) for _, evaluation in selected if name in evaluation.conditions]
            conditions.append({
                'strategy': strategy, 'condition': name, 'observations': len(selected),
                'evaluated': len(values), 'passed': sum(values), 'failed': len(values) - sum(values),
                'not_reached': len(selected) - len(values),
                'pass_rate_evaluated_pct': round(sum(values) / len(values) * 100, 2) if values else None,
            })
        counts = Counter(code for _, evaluation in selected for code in evaluation.rejected)
        for code, count in counts.most_common():
            rejections.append({'strategy': strategy, 'reason': code, 'count': count,
                               'share_of_observations_pct': round(count / len(selected) * 100, 2)})
    return conditions, rejections


def detect_moves(data: pd.DataFrame, main_contexts: list[dict], baseline_candidates: list[dict]) -> list[dict]:
    context_map = {(ctx['symbol'], ctx['ts']): ctx for ctx in main_contexts}
    moves = []
    for (symbol, day), rows in data.groupby(['symbol', 'session']):
        if day not in MAIN_DAYS:
            continue
        frame = rows.sort_values('timestamp').reset_index(drop=True).copy()
        frame['atr'] = atr(frame)
        flags = []
        for index in range(60, len(frame)):
            if index < 15 or not np.isfinite(frame['atr'].iloc[index]):
                continue
            start_price = float(frame['close'].iloc[index - 15])
            end_price = float(frame['close'].iloc[index])
            change = end_price / start_price - 1
            threshold = max(0.003, 2 * float(frame['atr'].iloc[index]) / start_price)
            direction = 'call' if change > 0 else 'put'
            sustained = end_price > float(frame['close'].iloc[index - 5]) if direction == 'call' else end_price < float(frame['close'].iloc[index - 5])
            if abs(change) >= threshold and sustained:
                flags.append((index, direction, change, threshold))
        groups = []
        for flag in flags:
            index, direction, _, _ = flag
            if groups and direction == groups[-1][-1][1] and index - groups[-1][-1][0] <= 10:
                groups[-1].append(flag)
            else:
                groups.append([flag])
        ranked = []
        for group in groups:
            first, last = group[0], group[-1]
            start_index = first[0] - 15
            move = float(frame['close'].iloc[last[0]]) / float(frame['close'].iloc[start_index]) - 1
            if abs(move) < 0.0035:
                continue
            detection_ts = frame['timestamp'].iloc[first[0]] + pd.Timedelta(minutes=1)
            end_ts = frame['timestamp'].iloc[last[0]] + pd.Timedelta(minutes=1)
            ctx = context_map.get((symbol, detection_ts))
            trend = 'UNKNOWN'
            reasons = []
            if ctx:
                trend = ctx['evaluations'][0].trend
                reasons = sorted({code for evaluation in ctx['evaluations'] for code in evaluation.rejected})
            start_ts = frame['timestamp'].iloc[start_index] + pd.Timedelta(minutes=1)
            candidates = [item for item in baseline_candidates if item['underlying'] == symbol and start_ts.isoformat() <= item['timestamp_utc'] <= end_ts.isoformat()]
            ranked.append({
                'underlying': symbol, 'session': day, 'direction': 'bullish' if move > 0 else 'bearish',
                'window_start_et': start_ts.tz_convert(ET).isoformat(),
                'first_observable_et': detection_ts.tz_convert(ET).isoformat(),
                'last_confirmed_et': end_ts.tz_convert(ET).isoformat(),
                'move_percent': round(move * 100, 3),
                'initial_15m_move_percent': round(first[2] * 100, 3),
                'detection_threshold_percent': round(first[3] * 100, 3),
                'bot_trend_at_detection': trend,
                'baseline_candidates_during_move': len(candidates),
                'candidate_scores': [float(item['technical_score']) for item in candidates],
                'primary_strategy_rejections_at_detection': reasons,
                'options_selection': 'NOT_EVALUATED_NO_ALIGNED_HISTORICAL_QUOTES',
                'risk_rules': 'NOT_EVALUATED_NO_HISTORICAL_ACCOUNT_STATE',
                'duplicate_suppression': 'NOT_APPLIED_BY_UNDERLYING_REPLAY',
            })
        moves.extend(sorted(ranked, key=lambda item: abs(item['move_percent']), reverse=True)[:3])
    return sorted(moves, key=lambda item: (item['session'], item['underlying'], item['first_observable_et']))


def session_metrics(data: pd.DataFrame) -> list[dict]:
    results = []
    for (symbol, day), rows in data.groupby(['symbol', 'session']):
        frame = rows.sort_values('timestamp')
        opened, closed = float(frame['open'].iloc[0]), float(frame['close'].iloc[-1])
        high, low = float(frame['high'].max()), float(frame['low'].min())
        ranges = atr(frame).dropna()
        results.append({
            'session': day, 'underlying': symbol, 'bars': len(frame),
            'open_to_close_percent': round((closed / opened - 1) * 100, 3),
            'range_percent': round((high - low) / opened * 100, 3),
            'directional_efficiency': round(abs(closed - opened) / (high - low), 3) if high > low else 0,
            'mean_atr_percent': round(float(ranges.mean()) / opened * 100, 3) if len(ranges) else None,
        })
    return sorted(results, key=lambda item: (item['session'], item['underlying']))


def main():
    data = pd.read_csv(DATA)
    data['timestamp'] = pd.to_datetime(data['timestamp'], utc=True)
    data = filter_regular_session(data)
    data['session'] = data['timestamp'].dt.tz_convert(ET).dt.date.astype(str)
    with SessionLocal() as session:
        settings = get_settings(session)
    print('Building chronological contexts...', flush=True)
    contexts = build_contexts(data, settings)
    main_contexts = [ctx for ctx in contexts if ctx['ts'].tz_convert(ET).date().isoformat() in MAIN_DAYS]
    variants = {
        'baseline': {},
        'relative_volume_1_0': {'volume': 1.0},
        'relative_volume_1_1': {'volume': 1.1},
        'vwap_weighted': {'vwap_mandatory': False},
        'ema_weighted': {'ema_mandatory': False},
        'confirmed_beyond_opening_no_fresh_cross': {'fresh_cross': False},
        'candle_weighted': {'candle_mandatory': False},
        'extension_3_0_atr': {'extension': 3.0},
        'extension_3_5_atr': {'extension': 3.5},
        'independent_trend_volatility_current_noop': {'regime': 'independent'},
        'volume_1_0_plus_vwap_weighted': {'volume': 1.0, 'vwap_mandatory': False},
        'volume_1_0_plus_extension_3_0': {'volume': 1.0, 'extension': 3.0},
        'confirmed_beyond_opening_plus_volume_1_0': {'fresh_cross': False, 'volume': 1.0},
    }
    all_observations = []
    variant_results = {}
    all_evaluations = {}
    for name, config in variants.items():
        print(f'Running {name}...', flush=True)
        observations, evaluations = run_variant(contexts, name, config, settings)
        all_observations.extend(observations)
        variant_results[name] = candidate_counts([item for item in observations if item['session'] in MAIN_DAYS])
        all_evaluations[name] = evaluations
    baseline = [item for item in all_observations if item['variant'] == 'baseline' and item['session'] in MAIN_DAYS]
    expected = json.loads((OUT / 'baseline.json').read_text())['decisions']
    actual = [(item['timestamp_utc'], item['underlying'], item['technical_score']) for item in baseline]
    reference = [(item['timestamp'], item['underlying'], item['technical_score']) for item in expected]
    if actual != reference:
        raise RuntimeError(f'Instrumented baseline differs from production replay: {actual!r} != {reference!r}')

    threshold_rows = []
    for threshold in (90, 85, 80, 78, 75, 70, 65, 60):
        meeting = [item for item in baseline if item['technical_score'] >= threshold]
        counts = candidate_counts(meeting)
        threshold_rows.append({
            'threshold': threshold, 'market_scans': len(main_contexts),
            'technical_observations': sum(len(ctx['evaluations']) for ctx in main_contexts),
            **counts,
            'candidates_rejected_by_scoring': len(baseline) - len(meeting),
            'meeting_after_stronger_confirmation': sum(item['stronger_confirmation_passed'] for item in meeting),
            'time_buckets': json.dumps(dict(Counter(item['time_bucket'] for item in meeting)), sort_keys=True),
            'options_rejected': '', 'risk_rejected': '',
            'options_status': 'NOT_EVALUATED_NO_ALIGNED_HISTORICAL_QUOTES',
            'risk_status': 'NOT_EVALUATED_NO_HISTORICAL_ACCOUNT_STATE',
            'allocation_gate': 'ZERO_BELOW_80' if threshold < 80 else 'NONE',
        })

    main_evaluations = [(ctx, evaluation) for ctx, evaluation in all_evaluations['baseline'] if ctx['ts'].tz_convert(ET).date().isoformat() in MAIN_DAYS]
    conditions, rejections = condition_rows(main_evaluations)
    moves = detect_moves(data, main_contexts, baseline)
    sessions = session_metrics(data)
    generalization = []
    for variant, observations in ((name, [item for item in all_observations if item['variant'] == name]) for name in variants):
        for day in sorted(set(data['session']) - MAIN_DAYS):
            selected = [item for item in observations if item['session'] == day]
            generalization.append({'variant': variant, 'session': day, **candidate_counts(selected)})

    pd.DataFrame(all_observations).to_csv(OUT / 'candidate_observations.csv', index=False)
    pd.DataFrame(threshold_rows).to_csv(OUT / 'threshold_comparison.csv', index=False)
    pd.DataFrame(conditions).to_csv(OUT / 'filter_condition_counts.csv', index=False)
    pd.DataFrame(rejections).to_csv(OUT / 'filter_rejection_counts.csv', index=False)
    pd.DataFrame(moves).to_csv(OUT / 'missed_opportunities.csv', index=False)
    pd.DataFrame(sessions).to_csv(OUT / 'session_metrics.csv', index=False)
    pd.DataFrame(generalization).to_csv(OUT / 'generalization.csv', index=False)
    serializable = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_data': str(DATA), 'source_rows': len(data),
        'baseline_reproduced': True, 'baseline_candidates': baseline,
        'thresholds': threshold_rows, 'variants': variant_results,
        'conditions': conditions, 'rejections': rejections,
        'moves': moves, 'sessions': sessions, 'generalization': generalization,
        'production_changes': 'NONE', 'orders_submitted': 0,
        'limitations': [
            'Technical scores are normalized to 0-100 and are not final option-aware production scores.',
            'Historical option selection and historical account risk decisions cannot be evaluated from underlying bars alone.',
            'Underlying replay does not apply the live bot same-candidate suppression cache.',
        ],
    }
    (OUT / 'analysis.json').write_text(json.dumps(serializable, indent=2, default=str))
    print(json.dumps({'baseline': variant_results['baseline'], 'variants': variant_results,
                      'moves': len(moves), 'output': str(OUT)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
