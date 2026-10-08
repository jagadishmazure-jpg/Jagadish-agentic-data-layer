#!/usr/bin/env bash
# Deployment steps used by .github/workflows/deploy.yml and teardown.yml. Each subcommand is
# idempotent and reads its inputs from the environment:
#   CLOUD        azure | gcp | aws
#   TARGET_ENV   dev | prod
#   AZURE_TOOL   terraform | bicep (Azure only)
#   TFSTATE_*    remote state location per cloud; GCP_PROJECT_ID; AWS_REGION
#   credentials  set by the OIDC login step of each cloud (never a secret)
#
#   deploy.sh provision   create/update the stack for $CLOUD
#   deploy.sh smoke       check the lake, the gold layer and the agents' read-only access exist
#   deploy.sh destroy     tear the environment down (teardown workflow only)
#
# Never run from this repository so far: DEPLOY_ENABLED is not set.
set -euo pipefail

CLOUD="${CLOUD:?CLOUD is required}"
ENV_NAME="${TARGET_ENV:?TARGET_ENV is required}"
STACK="infra/terraform/${CLOUD}"

tf_init() {
  case "$CLOUD" in
    azure)
      terraform -chdir="$STACK" init -input=false -backend-config="envs/${ENV_NAME}.backend.hcl" \
        -backend-config="resource_group_name=${TFSTATE_AZURE_RESOURCE_GROUP:?}" \
        -backend-config="storage_account_name=${TFSTATE_AZURE_STORAGE_ACCOUNT:?}" \
        -backend-config="container_name=tfstate" ;;
    gcp)
      terraform -chdir="$STACK" init -input=false -backend-config="envs/${ENV_NAME}.backend.hcl" \
        -backend-config="bucket=${TFSTATE_GCS_BUCKET:?}" ;;
    aws)
      terraform -chdir="$STACK" init -input=false -backend-config="envs/${ENV_NAME}.backend.hcl" \
        -backend-config="bucket=${TFSTATE_S3_BUCKET:?}" -backend-config="region=${AWS_REGION:-us-east-1}" ;;
    *) echo "unknown cloud $CLOUD"; exit 1 ;;
  esac
}

tf_vars() {
  local extra=()
  [[ "$CLOUD" == "gcp" ]] && extra+=(-var "project_id=${GCP_PROJECT_ID:?}")
  echo -var-file="envs/${ENV_NAME}.tfvars" "${extra[@]}"
}

provision() {
  if [[ "$CLOUD" == "azure" && "${AZURE_TOOL:-terraform}" == "bicep" ]]; then
    private=false
    [[ "$ENV_NAME" == "prod" ]] && private=true
    az deployment sub create --name "adl-${ENV_NAME}-${GITHUB_RUN_ID:-local}" --location eastus2 \
      --template-file infra/bicep/main.bicep --parameters environment="$ENV_NAME" privateNetworking="$private" -o none
    return
  fi
  tf_init
  # shellcheck disable=SC2046
  terraform -chdir="$STACK" apply -auto-approve -input=false $(tf_vars)
}

smoke() {
  case "$CLOUD" in
    azure)
      rg="rg-adl-retail-${ENV_NAME}-eus2-001"
      account=$(az storage account list -g "$rg" --query "[0].name" -o tsv)
      az storage container show --account-name "$account" --name gold --auth-mode login -o none
      [[ "$(az storage account show -n "$account" --query allowSharedKeyAccess -o tsv)" == "false" ]] || { echo "::error::shared keys enabled"; exit 1; }
      [[ "$(az search service list -g "$rg" --query "[0].disableLocalAuth" -o tsv)" == "true" ]] || { echo "::error::search keys enabled"; exit 1; } ;;
    gcp)
      bq show --format=none "${GCP_PROJECT_ID}:retail_gold"
      gcloud storage buckets describe "gs://${GCP_PROJECT_ID}-adl-retail-${ENV_NAME}-lake" --format="value(public_access_prevention)" | grep -q enforced ;;
    aws)
      aws glue get-database --name retail_gold >/dev/null
      aws athena get-work-group --work-group "adl-retail-${ENV_NAME}" --query "WorkGroup.Configuration.EnforceWorkGroupConfiguration" | grep -q true ;;
  esac
  echo "smoke checks passed for ${CLOUD}/${ENV_NAME}"
}

destroy() {
  if [[ "$CLOUD" == "azure" && "${AZURE_TOOL:-terraform}" == "bicep" ]]; then
    az group delete --name "rg-adl-retail-${ENV_NAME}-eus2-001" --yes
    return
  fi
  tf_init
  # shellcheck disable=SC2046
  terraform -chdir="$STACK" destroy -auto-approve -input=false $(tf_vars)
}

"$@"
