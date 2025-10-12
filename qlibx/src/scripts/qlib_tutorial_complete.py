"""
Qlib 量化交易完整入门教程
从数据获取到策略回测的完整流程
"""

import qlib
from qlib.config import REG_CN
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
import pandas as pd
import numpy as np

# ============================================================================
# 第一部分：初始化和基础数据获取
# ============================================================================
def tutorial():
    print("=" * 70)
    print("第一部分：Qlib 初始化和基础数据获取")
    print("=" * 70)

    # 初始化 Qlib
    qlib.init(
        provider_uri="/Users/Zeyu/Documents/q_trade/qlibx/data/qlib_data/cn_data",
        region=REG_CN
    )
    print("✓ Qlib 初始化完成\n")

    # 1.1 获取单只股票的基础数据
    print("【1.1】获取单只股票的基础数据")
    print("-" * 70)

    stock = "SH600000"  # 浦发银行
    start_date = "2024-01-01"
    end_date = "2024-01-31"

    df_basic = D.features(
        instruments=[stock],
        fields=["$open", "$high", "$low", "$close", "$volume"],
        start_time=start_date,
        end_time=end_date
    )

    print(f"股票: {stock}")
    print(f"数据条数: {len(df_basic)}")
    print(df_basic.head())

    # 1.2 计算简单的技术指标
    print("\n【1.2】计算技术指标（移动平均线）")
    print("-" * 70)

    df_ma = D.features(
        instruments=[stock],
        fields=[
            "$close",
            "Mean($close, 5)",   # 5日均线
            "Mean($close, 10)",  # 10日均线
            "Mean($close, 20)",  # 20日均线
        ],
        start_time=start_date,
        end_time=end_date
    )

    df_ma.columns = ["close", "MA5", "MA10", "MA20"]
    print(df_ma.tail(10))

    # 1.3 计算收益率和波动率
    print("\n【1.3】计算收益率和波动率")
    print("-" * 70)

    df_returns = D.features(
        instruments=[stock],
        fields=[
            "$close",
            "Ref($close, 1)",                            # 昨日收盘
            "$close / Ref($close, 1) - 1",              # 日收益率
            "Mean($close / Ref($close, 1) - 1, 20)",    # 20日平均收益
            "Std($close / Ref($close, 1) - 1, 20)",     # 20日波动率
        ],
        start_time=start_date,
        end_time=end_date
    )

    df_returns.columns = ["close", "pre_close", "return", "avg_return_20d", "volatility_20d"]
    print(df_returns.tail(10))

    print(f"\n统计信息:")
    print(f"平均日收益率: {df_returns['return'].mean():.4%}")
    print(f"收益率标准差: {df_returns['return'].std():.4%}")
    print(f"最大单日涨幅: {df_returns['return'].max():.4%}")
    print(f"最大单日跌幅: {df_returns['return'].min():.4%}")

    # ============================================================================
    # 第二部分：多股票数据处理和因子计算
    # ============================================================================

    print("\n\n" + "=" * 70)
    print("第二部分：多股票数据处理和因子计算")
    print("=" * 70)

    # 2.1 获取多只股票数据
    print("\n【2.1】获取多只股票数据")
    print("-" * 70)

    stocks = ["SH600000", "SH600036", "SH601318", "SH600519"]  # 浦发、招行、平安、茅台
    stock_names = ["浦发银行", "招商银行", "平安保险", "贵州茅台"]

    df_multi = D.features(
        instruments=stocks,
        fields=["$close", "$volume"],
        start_time="2024-01-01",
        end_time="2024-01-10"
    )

    print("多股票数据结构:")
    print(df_multi.head(20))

    # 2.2 计算常用 Alpha 因子
    print("\n【2.2】计算常用 Alpha 因子")
    print("-" * 70)

    # 定义一组常用的 Alpha 因子
    alpha_factors = {
        "close": "$close",
        
        # 动量因子
        "return_1d": "$close / Ref($close, 1) - 1",
        "return_5d": "$close / Ref($close, 5) - 1",
        "return_20d": "$close / Ref($close, 20) - 1",
        
        # 均值回归因子
        "ma5_ratio": "$close / Mean($close, 5) - 1",
        "ma20_ratio": "$close / Mean($close, 20) - 1",
        
        # 波动率因子
        "volatility_20d": "Std($close / Ref($close, 1) - 1, 20)",
        
        # 成交量因子
        "volume_ratio": "$volume / Mean($volume, 20)",
        
        # 价量相关性
        "price_volume_corr": "Corr($close, $volume, 10)",
    }

    df_alpha = D.features(
        instruments=["SH600000"],
        fields=list(alpha_factors.values()),
        start_time="2024-01-01",
        end_time="2024-01-31"
    )

    df_alpha.columns = list(alpha_factors.keys())
    print(df_alpha.tail(10))

    # 2.3 因子标准化（横截面）
    print("\n【2.3】因子标准化示例")
    print("-" * 70)

    # 对多只股票计算因子并标准化
    df_factor = D.features(
        instruments=stocks,
        fields=[
            "$close / Ref($close, 20) - 1",  # 20日收益率
        ],
        start_time="2024-01-01",
        end_time="2024-01-31"
    )

    # 提取某一天的数据进行横截面分析
    last_date = df_factor.index.get_level_values('datetime')[-1]
    df_cross_section = df_factor.xs(last_date, level='datetime')
    df_cross_section.columns = ['return_20d']

    print(f"日期: {last_date.date()}")
    print("\n各股票的20日收益率:")
    for i, stock in enumerate(stocks):
        if stock in df_cross_section.index:
            ret = df_cross_section.loc[stock, 'return_20d']
            print(f"  {stock_names[i]:8s}: {ret:7.2%}")

    # ============================================================================
    # 第三部分：使用 Alpha158 因子库
    # ============================================================================

    print("\n\n" + "=" * 70)
    print("第三部分：使用 Alpha158 因子库")
    print("=" * 70)

    from qlib.contrib.data.handler import Alpha158

    print("\n【3.1】Alpha158 因子简介")
    print("-" * 70)
    print("""
    Alpha158 是 Qlib 内置的 158 个技术因子，包括：
    - KBAR: K线相关因子（开高低收）
    - KDJ: KDJ 指标
    - RSI: 相对强弱指标
    - BIAS: 乖离率
    - BOLL: 布林带
    - ROC: 变动率指标
    - MA: 移动平均线
    - EMA: 指数移动平均
    - MACD: 指标
    - 成交量相关因子
    等等...
    """)

    # 创建 Alpha158 数据处理器
    print("【3.2】加载 Alpha158 因子")
    print("-" * 70)

    # 配置数据处理器
    handler_config = {
        "start_time": "2024-01-01",
        "end_time": "2024-01-31",
        "fit_start_time": "2024-01-01",
        "fit_end_time": "2024-01-31",
        "instruments": ["SH600000"],
    }

    # 初始化 Alpha158
    alpha158 = Alpha158(**handler_config)

    # 获取处理后的数据
    print("正在加载 Alpha158 因子...")
    try:
        # 注意：Alpha158 会计算大量因子，可能需要一些时间
        df_alpha158 = alpha158.fetch()
        
        print(f"✓ Alpha158 因子加载完成")
        print(f"  数据形状: {df_alpha158.shape}")
        print(f"  因子数量: {df_alpha158.shape[1]}")
        print(f"\n前10个因子:")
        print(df_alpha158.columns[:10].tolist())
        print(f"\n最后5条数据的前5个因子:")
        print(df_alpha158.iloc[-5:, :5])
        
    except Exception as e:
        print(f"⚠️  加载 Alpha158 出错: {e}")
        print("这可能是因为数据时间范围太短，Alpha158 需要至少60天的数据来计算一些长期因子")

    # ============================================================================
    # 第四部分：简单的选股策略示例
    # ============================================================================

    print("\n\n" + "=" * 70)
    print("第四部分：简单的选股策略")
    print("=" * 70)

    # 4.1 基于动量的选股策略
    print("\n【4.1】动量选股策略")
    print("-" * 70)

    # 选择一个股票池
    stock_pool = ["SH600000", "SH600036", "SH601318", "SH600519", 
                "SH600030", "SH601166", "SH601288", "SH600887"]

    # 计算20日动量
    momentum_data = D.features(
        instruments=stock_pool,
        fields=[
            "$close",
            "$close / Ref($close, 20) - 1",  # 20日收益率作为动量因子
        ],
        start_time="2024-01-01",
        end_time="2024-01-31"
    )

    momentum_data.columns = ["close", "momentum_20d"]

    # 选择最后一个交易日的数据
    last_day = momentum_data.index.get_level_values('datetime')[-1]
    momentum_last = momentum_data.xs(last_day, level='datetime').dropna()
    momentum_last = momentum_last.sort_values('momentum_20d', ascending=False)

    print(f"选股日期: {last_day.date()}")
    print(f"\n动量排名（20日收益率）:")
    print(momentum_last)

    # 选择动量最高的前3只股票
    top_3 = momentum_last.head(3)
    print(f"\n✓ 选中的前3只股票:")
    for stock in top_3.index:
        momentum = top_3.loc[stock, 'momentum_20d']
        print(f"  {stock}: 动量={momentum:.2%}")

    # 4.2 基于均值回归的选股策略
    print("\n【4.2】均值回归选股策略")
    print("-" * 70)

    # 计算价格相对于均线的偏离度
    mean_reversion_data = D.features(
        instruments=stock_pool,
        fields=[
            "$close",
            "($close - Mean($close, 20)) / Std($close, 20)",  # 标准化偏离度
        ],
        start_time="2024-01-01",
        end_time="2024-01-31"
    )

    mean_reversion_data.columns = ["close", "deviation"]

    # 选择最后一个交易日的数据
    last_day_mr = mean_reversion_data.index.get_level_values('datetime')[-1]
    deviation_last = mean_reversion_data.xs(last_day_mr, level='datetime').dropna()
    deviation_last = deviation_last.sort_values('deviation', ascending=True)  # 偏离最负的最超卖

    print(f"选股日期: {last_day_mr.date()}")
    print(f"\n偏离度排名（负值表示超卖）:")
    print(deviation_last)

    # 选择最超卖的前3只股票
    oversold_3 = deviation_last.head(3)
    print(f"\n✓ 最超卖的前3只股票（均值回归机会）:")
    for stock in oversold_3.index:
        dev = oversold_3.loc[stock, 'deviation']
        print(f"  {stock}: 偏离度={dev:.2f}σ")

    # ============================================================================
    # 第五部分：实用工具函数
    # ============================================================================

    print("\n\n" + "=" * 70)
    print("第五部分：实用工具函数")
    print("=" * 70)

    # 5.1 获取交易日历
    print("\n【5.1】交易日历工具")
    print("-" * 70)

    calendar = D.calendar(start_time="2024-01-01", end_time="2024-12-31")
    print(f"2024年交易日数量: {len(calendar)}")
    print(f"前10个交易日: {calendar[:10].tolist()}")
    print(f"后10个交易日: {calendar[-10:].tolist()}")

    # 5.2 获取股票池
    print("\n【5.2】获取股票池")
    print("-" * 70)

    try:
        # 获取沪深300成分股
        csi300 = D.list_instruments(
            instruments="csi300",
            start_time="2024-01-01",
            end_time="2024-01-01"
        )
        print(f"沪深300成分股数量: {len(csi300) if csi300 is not None else 'N/A'}")
    except Exception as e:
        print(f"⚠️  {e}")

    # ============================================================================
    # 总结和下一步
    # ============================================================================

    print("\n\n" + "=" * 70)
    print("教程完成！下一步学习方向")
    print("=" * 70)

    print("""
    ✅ 你已经学会了:
    1. Qlib 初始化和基础数据获取
    2. 计算技术指标和因子
    3. 处理多股票数据
    4. 使用 Alpha158 因子库
    5. 实现简单的选股策略

    📚 接下来可以学习:
    1. 机器学习模型训练（LightGBM、XGBoost等）
    2. 完整的回测框架
    3. 组合优化和风险管理
    4. 策略评估指标
    5. 自定义因子开发

    💡 推荐的学习路径:
    - 步骤1: 深入理解因子计算和特征工程
    - 步骤2: 学习使用 Dataset 和 DataHandler
    - 步骤3: 训练预测模型
    - 步骤4: 实现交易策略
    - 步骤5: 完整回测和性能评估
    """)

    print("\n" + "=" * 70)

if __name__ == "__main__":
    tutorial()