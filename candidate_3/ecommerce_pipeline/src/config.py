from pathlib import path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Get path to root project directory (one folder up from src/)
base_dir = path(__file__).resolve().parent.parent
ENV_FILE_PATH = base_dir/".env"



class Settings(BaseSettings):
    # .env variables are mapped here
    postgres_user: str
    postgres_password: str
    postgres_host: str
    postgres_port: int
    postgres_db: str

    # Instructing pydantic-setting to load our variables from .env
    model_config = SettingsConfigDict(
        env_file = ENV_FILE_PATH,
        env_file_encoding = 'utf-8',
        extra = 'ignore'
        )
    
# Creating an instance of the Settings class
settings = Settings()
    