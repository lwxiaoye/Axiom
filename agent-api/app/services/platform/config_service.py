from app.schemas.schemas import MasterConfig, MasterConfigUpdate


class ConfigService:
    def __init__(self):
        # In-memory storage for demo
        self.config = MasterConfig(
            base_url="",
            api_key="",
            model="qwen2.5:7b",
            system_prompt="你是一个 helpful 的 AI 助手。",
            welcome_message="你好！我是 AXIOM Agent，有什么可以帮你的吗？"
        )
    
    async def get_master_config(self) -> MasterConfig:
        """获取主智能体配置"""
        return self.config
    
    async def save_master_config(self, config: MasterConfigUpdate):
        """保存主智能体配置"""
        if config.base_url is not None:
            self.config.base_url = config.base_url
        if config.api_key is not None:
            self.config.api_key = config.api_key
        if config.model is not None:
            self.config.model = config.model
        if config.system_prompt is not None:
            self.config.system_prompt = config.system_prompt
        if config.welcome_message is not None:
            self.config.welcome_message = config.welcome_message


# Create singleton instance
config_service = ConfigService()
