# -*- coding: utf-8 -*-
import os

from structlog import get_logger

from google.cloud import asset_v1p5beta1

from google.protobuf.json_format import MessageToDict

from ..gcp_config import RESOURCE_TYPE_REQUESTS, ASSET_TYPES, POD_STATUS
from ..base import BaseGCPManager


logger = get_logger(__file__)


class GCPResourceManager(BaseGCPManager):
    def __init__(self, **kwargs):
        self.bucket_name = os.getenv("CM_STORAGE") or "cloudmatos-dev"
        self.SCOPES = []
        try:
            super(GCPResourceManager, self).__init__()
            self.client = asset_v1p5beta1.AssetServiceClient(credentials=self.credentials)
            content_type = asset_v1p5beta1.ContentType.RESOURCE
            self.request = {"content_type": content_type}
            self.resources = {}
        except Exception as ex:
            logger.bind().exception(ex)
            raise Exception(ex)

    def get_resources(
        self,
        project_id: str,
        resource_type: str,
        resource_names: list,
        resources: dict = None,
        next_page_token: str = None,
        mode: str = 'all'
    ):

        # Step by step will add the provision to fetch all resource type details e.g. pod, services etc.
        request = {
            # "asset_types": [RESOURCE_TYPE_REQUESTS[resource_type]],
            "asset_types": RESOURCE_TYPE_REQUESTS[resource_type],
            "parent": f"projects/{project_id if project_id is not None else self.projectId}",
            **self.request,
        }

        if next_page_token:
            request["page_token"] = next_page_token

        response = self.client.list_assets(request=request)
        response_dict = MessageToDict(response._pb)
        assets = response_dict.get("assets") or []
        resources = resources or {}

        for resource in assets:
            current_resource_type = ASSET_TYPES[resource["assetType"]]

            resource_type_plural = (
                "clusters"
                if resource_type == "cluster"
                else "instances"
                if resource_type == "instance"
                else "networks"
                if resource_type == "network"
                else "storages"
                if resource_type == 'storage'
                else "serviceAccounts"
                if resource_type == 'serviceAccount'
                else resource_type
            )

            resource_name_split = resource["name"].split("/")
            try:
                current_resource_name = resource_name_split[
                    resource_name_split.index(resource_type_plural) + 1
                ]
            except:
                current_resource_name = ""
            # if mode is not 'all' and current_resource_name not in resource_names:
            #     continue

            if resource_type == "cluster" and resource_type != current_resource_type:
                resource["cluster_name"] = current_resource_name
            if resource_type == 'network' and resource_type != current_resource_type:
                resource["network_name"] = resource['resource']['data']['network'].split('/')[-1]
            if resource_type == 'serviceAccount' and resource_type != current_resource_type:
                resource["service_account"] = current_resource_name
            if current_resource_type == "pods" and resource['resource']['data']['status']['phase'] not in POD_STATUS:
                continue

            existing_resources = resources.get(current_resource_type) or []
            existing_resources.append(resource)
            resources[current_resource_type] = existing_resources

        next_page_token = response_dict.get("nextPageToken")
        return resources, next_page_token

    def get_assets_inventory(self, resource, **kwargs):
        """
        Export Google Cloud get_assets_inventoryResources as an assets data.
        """

        log = logger.new()

        resource_type = resource.get('type', '')
        resource_name = resource.get('name', None)
        project_id = resource.get('project_id', None)

        try:
            gcp_resources, next_page_token = self.get_resources(
                project_id, resource_type, [resource_name] if resource_name else []
            )

            while next_page_token:
                log.info(f"Next page token identified: {next_page_token}")

                gcp_resources, next_page_token = self.get_resources(
                    project_id,
                    resource_type,
                    [resource_name],
                    gcp_resources,
                    next_page_token
                )
            log.info(f"Resources fetched from GCP.{resource_type}")
            resource['details'] = gcp_resources
        except Exception as ex:
            log.error("Error while calling list_assets again.",
                      error_message=str(ex))
            raise Exception(ex)
        return resource

    def _get_assets_inventory(self,
                              project_id: str,
                              resource_type: str,
                              resource_names: str = None,
                              next_page_token: str = None
                              ):
        """
        Export Google Cloud get_assets_inventoryResources as an assets data.
        """
        log = logger.new(
            resource_type=resource_type,
            resource_name=resource_names,
            next_page_token=next_page_token,
        )

        # Step by step will add the provision to fetch all resource type details e.g. pod, services etc.
        request = {
            "asset_types": [RESOURCE_TYPE_REQUESTS[resource_type]],
            "parent": f"projects/{project_id}",
            **self.request,
        }

        if next_page_token:
            request["page_token"] = next_page_token

        resource_details = None
        try:
            response = self.client.list_assets(request=request)
            response_dict = MessageToDict(response._pb)
            next_page_token = response_dict.get("nextPageToken")
            assets = response_dict.get("assets") or []
            if resource_names:
                for resource in assets:
                    if resource["resource"]["data"]["name"] in resource_names:
                        resource_details = resource["resource"]["data"]
                        log.info("resource details fetched successfully.")
                        break
                else:
                    if not resource_details:
                        next_page_token = getattr(
                            response._response, "next_page_token", None
                        )
                        log.info("resource details requesting again.")
                        if next_page_token:
                            return self.get_assets_inventory(
                                resource_type, resource_names, next_page_token
                            )
            else:
                resource_details = assets
                log.info(
                    f"All resources of type {resource_type} fetched successfully.")

        except Exception as ex:
            log.error("Error while calling list_assets again.",
                      error_message=str(ex))
            raise Exception(ex)

        # Cluster Nodes
        if resource_details and resource_type == "cluster":
            instances = self.get_cluster_instances(
                resource_details.get("instanceGroupUrls")
            )
            resource_details["instances"] = instances

        return resource_details

    def get_cluster_instances(self, instance_group_urls: list = []):
        instance_grp_urls = instance_group_urls[:]
        instances_list = []
        instances = self.get_assets_inventory(resource_type="instance")

        for instance in instances:
            instance = instance["resource"]["data"]
            name = instance["name"]
            if not instance_grp_urls:
                break
            for instance_grp_url in instance_grp_urls:
                instance_grp_id = instance_grp_url.split(
                    "/")[-1].replace("-grp", "")

                if instance_grp_id in name:
                    instances_list.append(instance)
                    instance_grp_urls.remove(instance_grp_url)
                    break

        return instances_list
