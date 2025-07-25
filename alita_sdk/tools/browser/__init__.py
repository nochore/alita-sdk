from typing import List, Optional, Literal
from langchain_core.tools import BaseTool, BaseToolkit

from pydantic import create_model, BaseModel, ConfigDict, Field, SecretStr, model_validator

from langchain_community.utilities.google_search import GoogleSearchAPIWrapper
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from .google_search_rag import GoogleSearchResults
from .crawler import SingleURLCrawler, MultiURLCrawler, GetHTMLContent, GetPDFContent
from .wiki import WikipediaQueryRun
from ..utils import get_max_toolkit_length, clean_string, TOOLKIT_SPLITTER
from logging import getLogger

logger = getLogger(__name__)

name = "browser"


def get_tools(tool):
    return BrowserToolkit().get_toolkit(
        selected_tools=tool['settings'].get('selected_tools', []),
        google_api_key=tool['settings'].get('google_api_key'),
        google_cse_id=tool['settings'].get("google_cse_id"),
        toolkit_name=tool.get('toolkit_name', '')
    ).get_tools()


class BrowserToolkit(BaseToolkit):
    tools: List[BaseTool] = []

    @staticmethod
    def toolkit_config_schema() -> BaseModel:
        selected_tools = {
            'single_url_crawler': SingleURLCrawler.__pydantic_fields__['args_schema'].default.schema(),
            'multi_url_crawler': MultiURLCrawler.__pydantic_fields__['args_schema'].default.schema(),
            'get_html_content': GetHTMLContent.__pydantic_fields__['args_schema'].default.schema(),
            'get_pdf_content': GetPDFContent.__pydantic_fields__['args_schema'].default.schema(),
            'google': GoogleSearchResults.__pydantic_fields__['args_schema'].default.schema(),
            'wiki': WikipediaQueryRun.__pydantic_fields__['args_schema'].default.schema()
        }
        BrowserToolkit.toolkit_max_length = get_max_toolkit_length(selected_tools)

        def validate_google_fields(cls, values):
            if 'google' in values.get('selected_tools', []):
                google_cse_id = values.get('google_cse_id') is not None
                google_api_key = values.get('google_api_key') is not None
                if not (google_cse_id and google_api_key):
                    raise ValueError("google_cse_id and google_api_key are required when 'google' is in selected_tools")
            return values

        return create_model(
            name,
            __config__=ConfigDict(json_schema_extra={'metadata': {"label": "Browser", "icon_url": None, "categories": ["testing"], "extra_categories": ["web scraping", "search", "crawler"]}}),
            google_cse_id=(Optional[str], Field(description="Google CSE id", default=None)),
            google_api_key=(Optional[SecretStr],
                            Field(description="Google API key", default=None, json_schema_extra={'secret': True})),
            selected_tools=(List[Literal[tuple(selected_tools)]],
                            Field(default=[], json_schema_extra={'args_schemas': selected_tools})),
            __validators__={
                "validate_google_fields": model_validator(mode='before')(validate_google_fields)
            }
        )

    @classmethod
    def get_toolkit(cls, selected_tools: list[str] | None = None, toolkit_name: Optional[str] = None, **kwargs):
        if selected_tools is None:
            selected_tools = []
        tools = []
        prefix = clean_string(toolkit_name, cls.toolkit_max_length) + TOOLKIT_SPLITTER if toolkit_name else ''
        if not selected_tools:
            selected_tools = [
                'single_url_crawler',
                'multi_url_crawler',
                'get_html_content',
                'google',
                'wiki']
        for tool in selected_tools:
            tool_entry = None  # Initialize tool_entry to None for each iteration
            
            if tool == 'single_url_crawler':
                tool_entry = SingleURLCrawler()
            elif tool == 'multi_url_crawler':
                tool_entry = MultiURLCrawler()
            elif tool == 'get_html_content':
                tool_entry = GetHTMLContent()
            elif tool == 'get_pdf_content':
                tool_entry = GetPDFContent()
            elif tool == 'google':
                try:
                    google_api_wrapper = GoogleSearchAPIWrapper(
                        google_api_key=kwargs.get("google_api_key"),
                        google_cse_id=kwargs.get("google_cse_id"),
                    )
                    tool_entry = GoogleSearchResults(api_wrapper=google_api_wrapper)
                    # rename the tool to avoid conflicts
                    tool_entry.name = "google"
                except Exception as e:
                    logger.error(f"Google API Wrapper failed to initialize: {e}")
            elif tool == 'wiki':
                tool_entry = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
                # rename the tool to avoid conflicts
                tool_entry.name = "wiki"

            # Only add the tool if it was successfully created
            if tool_entry is not None:
                tool_entry.name = f"{prefix}{tool_entry.name}"
                tools.append(tool_entry)
        return cls(tools=tools)

    def get_tools(self):
        return self.tools
