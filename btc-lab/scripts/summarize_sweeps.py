"""Summarise results/sweep_*.csv (counts, medians, best rows, means by target/entry/stop/trend)."""
import glob
import pandas as pd
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
cols = ['entry','tp','sl','trend_n','total_return_pct','buy_hold_return_pct','n_trades','win_rate_pct','expectancy_pct_per_trade','max_drawdown_pct','fees_pct_of_initial','exposure_pct','avg_bars_held','trades_per_month']
for f in sorted(glob.glob('results/sweep_*.csv')):
    d = pd.read_csv(f)
    print(f"\n##### {f}: {len(d)} configs; hold={d['buy_hold_return_pct'].iloc[0]}%  period {d['start'].iloc[0][:10]} -> {d['end'].iloc[0][:10]}")
    print(f"beats hold: {(d['total_return_pct']>d['buy_hold_return_pct']).sum()}/{len(d)}; positive: {(d['total_return_pct']>0).sum()}/{len(d)}; positive expectancy/trade: {(d['expectancy_pct_per_trade']>0).sum()}/{len(d)}; with a stop AND positive: {((d['sl'].notna())&(d['total_return_pct']>0)).sum()}/{int(d['sl'].notna().sum())}")
    print("median total return %.1f%%; median expectancy/trade %.3f%%" % (d['total_return_pct'].median(), d['expectancy_pct_per_trade'].median()))
    print("-- top 6 by total return"); print(d.sort_values('total_return_pct', ascending=False)[cols].head(6).to_string(index=False))
    print("-- top 4 WITH a stop-loss"); print(d[d['sl'].notna()].sort_values('total_return_pct', ascending=False)[cols].head(4).to_string(index=False))
    print("-- bottom 3"); print(d.sort_values('total_return_pct')[cols].head(3).to_string(index=False))
    print("-- by target (mean over entries/stops/trends)")
    print(d.groupby('tp').agg(ret=('total_return_pct','mean'), ret_med=('total_return_pct','median'), exp=('expectancy_pct_per_trade','mean'), win=('win_rate_pct','mean'), trades=('n_trades','mean'), mdd=('max_drawdown_pct','mean')).round(2).to_string())
    print("-- by entry"); print(d.groupby('entry').agg(ret=('total_return_pct','mean'), ret_med=('total_return_pct','median'), exp=('expectancy_pct_per_trade','mean'), trades=('n_trades','mean')).round(2).to_string())
    print("-- by stop"); print(d.assign(sl=d['sl'].fillna('none')).groupby('sl').agg(ret=('total_return_pct','mean'), exp=('expectancy_pct_per_trade','mean'), win=('win_rate_pct','mean'), mdd=('max_drawdown_pct','mean')).round(2).to_string())
    print("-- by trend filter"); print(d.assign(trend_n=d['trend_n'].fillna('none')).groupby('trend_n').agg(ret=('total_return_pct','mean'), exp=('expectancy_pct_per_trade','mean'), trades=('n_trades','mean'), mdd=('max_drawdown_pct','mean')).round(2).to_string())
