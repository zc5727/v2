param(
    [string]$Target = "all"
)

python -m atm_ops_agent.agent atm check --config inventory.atm.toml --target $Target
