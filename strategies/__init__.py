from .sma_cross import SmaCross

# Registry to map strategy name strings to their corresponding strategy classes
STRATEGIES_REGISTRY = {
    "SmaCross": SmaCross
}

def get_strategy(strategy_name: str):
    """
    Retrieve a strategy class by its name.
    """
    return STRATEGIES_REGISTRY.get(strategy_name)
