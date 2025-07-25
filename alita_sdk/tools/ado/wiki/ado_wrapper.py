import logging
from typing import Any, Dict, Generator, List, Optional

from alita_sdk.tools.elitea_base import BaseIndexParams
from langchain_core.documents import Document

from ...elitea_base import BaseVectorStoreToolApiWrapper, extend_with_vector_tools
from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsServiceError
from azure.devops.v7_0.core import CoreClient
from azure.devops.v7_0.wiki import WikiClient, WikiPageCreateOrUpdateParameters, WikiCreateParametersV2, \
    WikiPageMoveParameters
from azure.devops.v7_0.wiki.models import GitVersionDescriptor
from langchain_core.tools import ToolException
from msrest.authentication import BasicAuthentication
from pydantic import create_model, PrivateAttr, SecretStr
from pydantic import model_validator
from pydantic.fields import Field
try:
    from alita_sdk.runtime.langchain.interfaces.llm_processor import get_embeddings
except ImportError:
    from alita_sdk.langchain.interfaces.llm_processor import get_embeddings

from ...elitea_base import BaseToolApiWrapper

logger = logging.getLogger(__name__)

GetWikiInput = create_model(
    "GetWikiInput",
    wiki_identified=(str, Field(description="Wiki ID or wiki name"))
)

GetPageByPathInput = create_model(
    "GetPageByPathInput",
    wiki_identified=(str, Field(description="Wiki ID or wiki name")),
    page_name=(str, Field(description="Wiki page path"))
)

GetPageByIdInput = create_model(
    "GetPageByIdInput",
    wiki_identified=(str, Field(description="Wiki ID or wiki name")),
    page_id=(int, Field(description="Wiki page ID"))
)

ModifyPageInput = create_model(
    "ModifyPageInput",
    wiki_identified=(str, Field(description="Wiki ID or wiki name")),
    page_name=(str, Field(description="Wiki page name")),
    page_content=(str, Field(description="Wiki page content")),
    version_identifier=(str, Field(description="Version string identifier (name of tag/branch, SHA1 of commit)")),
    version_type=(Optional[str], Field(description="Version type (branch, tag, or commit). Determines how Id is interpreted", default="branch"))
)

RenamePageInput = create_model(
    "RenamePageInput",
    wiki_identified=(str, Field(description="Wiki ID or wiki name")),
    old_page_name=(str, Field(description="Old Wiki page name to be renamed", examples= ["/TestPageName"])),
    new_page_name=(str, Field(description="New Wiki page name", examples= ["/RenamedName"])),
    version_identifier=(str, Field(description="Version string identifier (name of tag/branch, SHA1 of commit)")),
    version_type=(Optional[str], Field(description="Version type (branch, tag, or commit). Determines how Id is interpreted", default="branch"))
)

# Schema for indexing ADO Wiki pages into vector store
indexData = create_model(
    "indexData",
    __base__=BaseIndexParams,
    wiki_identifier=(str, Field(description="Wiki identifier to index, e.g., 'ABCProject.wiki'")),
    progress_step=(Optional[int], Field(default=None, ge=0, le=100,
                         description="Optional step size for progress reporting during indexing")),
    clean_index=(Optional[bool], Field(default=False,
                       description="Optional flag to enforce clean existing index before indexing new data")),
)


class AzureDevOpsApiWrapper(BaseVectorStoreToolApiWrapper):
    organization_url: str
    project: str
    token: SecretStr
    _client: Optional[WikiClient] = PrivateAttr()  # Private attribute for the wiki client
    _core_client: Optional[CoreClient] = PrivateAttr()  # Private attribute for the CoreClient client

    llm: Any = None
    connection_string: Optional[SecretStr] = None
    collection_name: Optional[str] = None
    embedding_model: Optional[str] = "HuggingFaceEmbeddings"
    embedding_model_params: Optional[Dict[str, Any]] = {"model_name": "sentence-transformers/all-MiniLM-L6-v2"}
    vectorstore_type: Optional[str] = "PGVector"

    class Config:
        arbitrary_types_allowed = True  # Allow arbitrary types (e.g., WorkItemTrackingClient)

    @model_validator(mode='before')
    @classmethod
    def validate_toolkit(cls, values):
        """Validate and set up the Azure DevOps client."""
        try:
            # Set up connection to Azure DevOps using Personal Access Token (PAT)
            credentials = BasicAuthentication('', values['token'])
            connection = Connection(base_url=values['organization_url'], creds=credentials)

            # Retrieve the work item tracking client and assign it to the private _client attribute
            cls._client = connection.clients.get_wiki_client()
            cls._core_client = connection.clients.get_core_client()

        except Exception as e:
            return ImportError(f"Failed to connect to Azure DevOps: {e}")

        return values

    def get_wiki(self, wiki_identified: str):
        """Extract ADO wiki information."""
        try:
            return self._client.get_wiki(project=self.project, wiki_identifier=wiki_identified)
        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki: {str(e)}")
            return ToolException(f"Error during the attempt to extract wiki: {str(e)}")

    def get_wiki_page_by_path(self, wiki_identified: str, page_name: str):
        """Extract ADO wiki page content."""
        try:
            return self._client.get_page(project=self.project, wiki_identifier=wiki_identified, path=page_name,
                                         include_content=True).page.content
        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki page: {str(e)}")
            return ToolException(f"Error during the attempt to extract wiki page: {str(e)}")

    def get_wiki_page_by_id(self, wiki_identified: str, page_id: int):
        """Extract ADO wiki page content."""
        try:
            return (self._client.get_page_by_id(project=self.project, wiki_identifier=wiki_identified, id=page_id,
                                                include_content=True).page.content)
        except Exception as e:
            logger.error(f"Error during the attempt to extract wiki page: {str(e)}")
            return ToolException(f"Error during the attempt to extract wiki page: {str(e)}")

    def delete_page_by_path(self, wiki_identified: str, page_name: str):
        """Extract ADO wiki page content."""
        try:
            self._client.delete_page(project=self.project, wiki_identifier=wiki_identified, path=page_name)
            return f"Page '{page_name}' in wiki '{wiki_identified}' has been deleted"
        except Exception as e:
            logger.error(f"Unable to delete wiki page: {str(e)}")
            return ToolException(f"Unable to delete wiki page: {str(e)}")

    def delete_page_by_id(self, wiki_identified: str, page_id: str):
        """Extract ADO wiki page content."""
        try:
            self._client.delete_page_by_id(project=self.project, wiki_identifier=wiki_identified, id=page_id)
            return f"Page with id '{page_id}' in wiki '{wiki_identified}' has been deleted"
        except Exception as e:
            logger.error(f"Unable to delete wiki page: {str(e)}")
            return ToolException(f"Unable to delete wiki page: {str(e)}")

    def rename_wiki_page(self, wiki_identified: str, old_page_name: str, new_page_name: str, version_identifier: str,
                         version_type: str = "branch"):
        """Rename page

        Args:
         wiki_identified (str): The identifier for the wiki.
         old_page_name (str): The current name of the page to be renamed (e.g. '/old_page_name').
         new_page_name (str): The new name for the page (e.g. '/new_page_name').
         version_identifier (str): The identifier for the version (e.g., branch or commit). Defaults to None.
         version_type (str, optional): The type of version identifier. Defaults to "branch".
     """

        try:
            try:
                return self._client.create_page_move(
                    project=self.project,
                    wiki_identifier=wiki_identified,
                    comment=f"Page rename from '{old_page_name}' to '{new_page_name}'",
                    page_move_parameters=WikiPageMoveParameters(new_path=new_page_name, path=old_page_name),
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type)
                )
            except AzureDevOpsServiceError as e:
                if "The version '{0}' either is invalid or does not exist." in str(e):
                    # Retry the request without version_descriptor
                    return self._client.create_page_move(
                        project=self.project,
                        wiki_identifier=wiki_identified,
                        comment=f"Page rename from '{old_page_name}' to '{new_page_name}'",
                        page_move_parameters=WikiPageMoveParameters(new_path=new_page_name, path=old_page_name),
                    )
                else:
                    raise
        except Exception as e:
            logger.error(f"Unable to rename wiki page: {str(e)}")
            return ToolException(f"Unable to rename wiki page: {str(e)}")

    def modify_wiki_page(self, wiki_identified: str, page_name: str, page_content: str, version_identifier: str, version_type: str = "branch"):
        """Create or Update ADO wiki page content."""
        try:
            all_wikis = [wiki.name for wiki in self._client.get_all_wikis(project=self.project)]
            if wiki_identified not in all_wikis:
                logger.info(f"wiki name '{wiki_identified}' doesn't exist. New wiki will be created.")
                try:
                    project_id = None
                    projects = self._core_client.get_projects()

                    for project in projects:
                        if project.name == self.project:
                            project_id = project.id
                            break
                    if project_id:
                        self._client.create_wiki(project=self.project, wiki_create_params=WikiCreateParametersV2(name=wiki_identified, project_id=project_id))
                    else:
                        return "Project ID has not been found."
                except Exception as create_wiki_e:
                    return ToolException(f"Unable to create new wiki due to error: {create_wiki_e}")
            try:
                page = self._client.get_page(project=self.project, wiki_identifier=wiki_identified, path=page_name)
                version = page.eTag
            except Exception as get_page_e:
                if "Ensure that the path of the page is correct and the page exists" in str(get_page_e):
                    logger.info("Path is not found. New page will be created")
                    version = None
                else:
                    return ToolException(f"Unable to extract page by path {page_name}: {str(get_page_e)}")

            try:
                return self._client.create_or_update_page(
                    project=self.project,
                    wiki_identifier=wiki_identified,
                    path=page_name,
                    parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                    version=version,
                    version_descriptor=GitVersionDescriptor(version=version_identifier, version_type=version_type)
                )
            except AzureDevOpsServiceError as e:
                if "The version '{0}' either is invalid or does not exist." in str(e):
                    # Retry the request without version_descriptor
                    return self._client.create_or_update_page(
                        project=self.project,
                        wiki_identifier=wiki_identified,
                        path=page_name,
                        parameters=WikiPageCreateOrUpdateParameters(content=page_content),
                        version=version
                    )
                else:
                    raise
        except Exception as e:
            logger.error(f"Unable to modify wiki page: {str(e)}")
            return ToolException(f"Unable to modify wiki page: {str(e)}")

    def index_data(
            self,
            wiki_identifier: str,
            collection_suffix: str = '',
            progress_step: int = None,
            clean_index: bool = False
    ):
        """Load ADO Wiki pages into the vector store."""
        docs = self._base_loader(wiki_identifier)
        embedding = get_embeddings(self.embedding_model, self.embedding_model_params)
        vs = self._init_vector_store(collection_suffix, embeddings=embedding)
        return vs.index_documents(docs, progress_step=progress_step, clean_index=clean_index)

    def _base_loader(self, wiki_identifier: str) -> Generator[Document, None, None]:
        pages = self._client.get_pages_batch(pages_batch_request={}, project=self.project, wiki_identifier=wiki_identifier)
        #
        for page in pages:
            content = self._client.get_page_by_id(project=self.project, wiki_identifier=wiki_identifier, id=page.id, include_content=True).page.content
            yield Document(page_content=content, metadata={
                'id': page.id, 
                'path': page.path,
                'updated_on': ''
            })

    def _process_document(self, document: Document) -> Generator[Document, None, None]:
        yield document

    @extend_with_vector_tools
    def get_available_tools(self):
        """Return a list of available tools."""
        return [
            {
                "name": "get_wiki",
                "description": self.get_wiki.__doc__,
                "args_schema": GetWikiInput,
                "ref": self.get_wiki,
            },
            {
                "name": "get_wiki_page_by_path",
                "description": self.get_wiki_page_by_path.__doc__,
                "args_schema": GetPageByPathInput,
                "ref": self.get_wiki_page_by_path,
            },
            {
                "name": "get_wiki_page_by_id",
                "description": self.get_wiki_page_by_id.__doc__,
                "args_schema": GetPageByIdInput,
                "ref": self.get_wiki_page_by_id,
            },
            {
                "name": "delete_page_by_path",
                "description": self.delete_page_by_path.__doc__,
                "args_schema": GetPageByPathInput,
                "ref": self.delete_page_by_path,
            },
            {
                "name": "delete_page_by_id",
                "description": self.delete_page_by_id.__doc__,
                "args_schema": GetPageByIdInput,
                "ref": self.delete_page_by_id,
            },
            {
                "name": "modify_wiki_page",
                "description": self.modify_wiki_page.__doc__,
                "args_schema": ModifyPageInput,
                "ref": self.modify_wiki_page,
            },
            {
                "name": "rename_wiki_page",
                "description": self.rename_wiki_page.__doc__,
                "args_schema": RenamePageInput,
                "ref": self.rename_wiki_page,
            },
            {
                "name": "index_data",
                "ref": self.index_data,
                "description": self.index_data.__doc__,
                "args_schema": indexData,
            }
        ]