"""
Qlib 简单回测示例
使用动量策略进行回测
"""

import qlib
from qlib.config import REG_CN
from qlib.data import D
# from qlib.backtest import backtest, executor # Not used in manual backtest
# from qlib.contrib.strategy import TopkDropoutStrategy # Not used in manual backtest
# from qlib.contrib.evaluate import risk_analysis # Not used in manual backtest
# from qlib.utils import init_instance_by_config # Not used in manual backtest
import pandas as pd
import warnings
warnings.filterwarnings('ignore')
import sys

# Wrap the main execution logic in a function
def run_backtest():
    # ============================================================================
    # 第一步：初始化
    # ============================================================================

    print("=" * 70)
    print("Qlib 回测示例 - 动量策略")
    print("=" * 70)

    # It's good practice to ensure the qlib init path exists
    # If the path is relative, it might cause issues if the script is run from a different directory.
    # For robustness, you might want to use an absolute path or check if the directory exists.
    # Example:
    # import os
    # provider_uri = os.path.join(os.getcwd(), "qlib_data", "cn_data") # If your qlib_data is in the current directory
    provider_uri = "/Users/Zeyu/Documents/q_trade/qlibx/data/qlib_data/cn_data" # Your original path

    qlib.init(
        provider_uri=provider_uri,
        region=REG_CN
    )
    print("✓ Qlib 初始化完成\n")

    # ============================================================================
    # 第二步：定义自己的简单策略
    # ============================================================================

    print("【步骤1】定义策略参数")
    print("-" * 70)

    # 策略参数
    STOCK_POOL = [
        "SH600000", "SH600036", "SH601318", "SH600519",
        "SH600030", "SH601166", "SH601288", "SH600887",
        "SH601398", "SH601939", "SH600016", "SH601328"
    ]

    START_TIME = "2024-01-01"
    END_TIME = "2024-01-31"
    INIT_CASH = 1000000  # 初始资金100万
    TOP_K = 3  # 持有前3只股票

    print(f"股票池: {len(STOCK_POOL)} 只股票")
    print(f"回测时间: {START_TIME} 至 {END_TIME}")
    print(f"初始资金: {INIT_CASH:,.0f} 元")
    print(f"持仓数量: 前 {TOP_K} 只")

    # ============================================================================
    # 第三步：生成交易信号
    # ============================================================================

    print("\n【步骤2】计算动量因子并生成信号")
    print("-" * 70)

    # 计算20日动量
    # Ensure correct qlib version compatibility for D.features.
    # The 'inst_processors' error could hint at an older or specific qlib version expecting different parameters,
    # or an internal issue where it's being passed implicitly.
    # For common usage, this call should be fine.
    # If the TypeError persists, it might indicate a bug in your qlib installation or a very old version.
    momentum_data = D.features(
        instruments=STOCK_POOL,
        fields=["$close / Ref($close, 20) - 1"],  # 20日收益率
        start_time=START_TIME,
        end_time=END_TIME
    )

    momentum_data.columns = ["score"]
    print(f"✓ 动量因子计算完成，数据形状: {momentum_data.shape}")

    # 查看最后一天的信号
    last_date = momentum_data.index.get_level_values('datetime')[-1]
    last_day_scores = momentum_data.xs(last_date, level='datetime').dropna()
    last_day_scores = last_day_scores.sort_values('score', ascending=False)

    print(f"\n最后交易日 ({last_date.date()}) 的动量排名:")
    print(last_day_scores.head(10))

    # ============================================================================
    # 第四步：手动实现简单回测
    # ============================================================================

    print("\n【步骤3】执行简单回测")
    print("-" * 70)

    # 获取所有交易日
    trade_dates = momentum_data.index.get_level_values('datetime').unique().sort_values()

    # 初始化回测状态
    cash = INIT_CASH
    holdings = {}  # {stock: shares}
    portfolio_value = []
    dates = []

    print(f"交易日数量: {len(trade_dates)}")

    # 获取价格数据
    price_data = D.features(
        instruments=STOCK_POOL,
        fields=["$close"],
        start_time=START_TIME,
        end_time=END_TIME
    )
    price_data.columns = ["close"]

    # 每5天调仓一次
    rebalance_freq = 5

    for i, date in enumerate(trade_dates):
        # 计算当前持仓市值
        holding_value = 0
        for stock, shares in holdings.items():
            try:
                current_price = price_data.loc[(stock, date), 'close']
                holding_value += shares * current_price
            except KeyError: # Use KeyError for missing index
                pass
        
        total_value = cash + holding_value
        portfolio_value.append(total_value)
        dates.append(date)
        
        # 调仓逻辑（每5天或第一天）
        if i % rebalance_freq == 0 or i == 0:
            # 清空所有持仓
            for stock, shares in holdings.items():
                try:
                    sell_price = price_data.loc[(stock, date), 'close']
                    cash += shares * sell_price
                except KeyError:
                    pass
            holdings = {}
            
            # 选择动量最高的 TOP_K 只股票
            try:
                day_scores = momentum_data.xs(date, level='datetime').dropna()
                day_scores = day_scores.sort_values('score', ascending=False)
                selected_stocks = day_scores.head(TOP_K).index.tolist()
                
                # 等权分配资金
                if selected_stocks: # Avoid division by zero if no stocks are selected
                    position_size = cash / len(selected_stocks)
                else:
                    position_size = 0 # No stocks to buy

                # 买入
                for stock in selected_stocks:
                    try:
                        buy_price = price_data.loc[(stock, date), 'close']
                        if buy_price > 0: # Avoid division by zero
                            shares = int(position_size / buy_price / 100) * 100  # 买100股的整数倍
                            if shares > 0:
                                cost = shares * buy_price
                                holdings[stock] = shares
                                cash -= cost
                    except KeyError:
                        pass
                
                if i == 0:
                    print(f"\n初始建仓 ({date.date()}):")
                    if selected_stocks:
                        for stock in selected_stocks:
                            if stock in holdings:
                                print(f"  {stock}: {holdings[stock]} 股")
                    else:
                        print("  无股票可建仓。")
                            
            except Exception as e:
                # print(f"Error during rebalancing on {date.date()}: {e}") # Optional: for debugging
                pass

    # ============================================================================
    # 第五步：回测结果分析
    # ============================================================================

    print("\n【步骤4】回测结果分析")
    print("-" * 70)

    # 创建结果 DataFrame
    result_df = pd.DataFrame({
        'date': dates,
        'portfolio_value': portfolio_value
    })
    result_df['date'] = pd.to_datetime(result_df['date'])
    result_df = result_df.set_index('date')

    # 计算收益率
    result_df['return'] = result_df['portfolio_value'].pct_change()
    result_df['cumulative_return'] = (1 + result_df['return']).cumprod() - 1

    print("\n组合表现:")
    print(result_df.tail(10))

    # 计算关键指标
    if not result_df.empty and len(result_df) > 1:
        total_return = (result_df['portfolio_value'].iloc[-1] / INIT_CASH - 1)
        n_days = len(result_df)
        annualized_return = (1 + total_return) ** (252 / n_days) - 1
        volatility = result_df['return'].std() * (252 ** 0.5)
        sharpe_ratio = (annualized_return - 0.03) / volatility if volatility > 0 else 0

        # 最大回撤
        cumulative = (1 + result_df['return']).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()
    else:
        total_return = 0
        annualized_return = 0
        volatility = 0
        sharpe_ratio = 0
        max_drawdown = 0
        n_days = 0
        print("回测结果数据不足，无法计算详细指标。")


    print(f"\n" + "=" * 70)
    print("回测结果统计")
    print("=" * 70)
    print(f"初始资金:     {INIT_CASH:,.0f} 元")
    print(f"最终资金:     {result_df['portfolio_value'].iloc[-1]:,.0f} 元" if not result_df.empty else "N/A")
    print(f"总收益率:     {total_return:.2%}")
    print(f"年化收益率:   {annualized_return:.2%}")
    print(f"年化波动率:   {volatility:.2%}")
    print(f"夏普比率:     {sharpe_ratio:.2f}")
    print(f"最大回撤:     {max_drawdown:.2%}")
    print(f"交易日数:     {n_days} 天")

    # 计算基准表现（等权买入持有）
    print("\n【步骤5】与基准对比")
    print("-" * 70)

    # 计算股票池的平均表现
    benchmark_returns = []
    for stock in STOCK_POOL:
        try:
            stock_prices = price_data.loc[stock]
            if len(stock_prices) > 1: # Need at least two prices to calculate return
                stock_return = stock_prices['close'].iloc[-1] / stock_prices['close'].iloc[0] - 1
                benchmark_returns.append(stock_return)
        except KeyError:
            pass

    if benchmark_returns:
        avg_benchmark_return = sum(benchmark_returns) / len(benchmark_returns)
        print(f"股票池平均收益率: {avg_benchmark_return:.2%}")
        print(f"策略超额收益:     {total_return - avg_benchmark_return:.2%}")
    else:
        print("无法计算基准收益 (可能因为股票数据不足)。")

    # ============================================================================
    # 总结
    # ============================================================================

    print("\n" + "=" * 70)
    print("回测完成！")
    print("=" * 70)

    print(f"""
    策略说明:
    - 策略类型: 动量策略（20日收益率）
    - 持仓数量: {TOP_K} 只
    - 调仓频率: 每 {rebalance_freq} 个交易日
    - 权重分配: 等权

    注意事项:
    1. 这是一个简化的回测，未考虑交易成本
    2. 实际交易会有滑点和手续费
    3. 股票数量有限，结果仅供学习参考
    4. 时间周期较短，统计意义有限

    下一步建议:
    - 使用更长的回测时间（如1年或更长）
    - 增加交易成本（手续费0.03%，印花税0.1%）
    - 测试不同的因子（如价值、质量、低波等）
    - 使用机器学习模型来预测股票收益
    - 实现更复杂的组合优化方法
    """)

# This is the crucial part for multiprocessing on Windows
if __name__ == '__main__':
    # Add freeze_support() if you intend to create a frozen executable
    # from multiprocessing import freeze_support
    # freeze_support()

    # The actual backtest logic is called here
    run_backtest()