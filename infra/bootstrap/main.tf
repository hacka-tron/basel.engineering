resource "random_id" "state_bucket" {
  byte_length = 8

  # The id is part of the bucket name: replacing it would rename (replace) the
  # state bucket that holds every Terraform state in this project.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket" "state" {
  bucket = "glassbox-tfstate-${var.aws_account_id}-${random_id.state_bucket.hex}"

  # Holds the state of this root (bootstrap/terraform.tfstate) and of
  # infra/envs/prod. Losing it is an outage, so Terraform must refuse to
  # destroy or replace it. Removing this line is a deliberate, reviewed act.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket-level guardrails. Nothing here widens access.
#   - DenyInsecureTransport: every request must use TLS.
#   - DenyDeleteBucket: nobody (not even the owner) can delete the bucket
#     without first removing this policy, a second deliberate step on top of
#     prevent_destroy.
#   - DenyCiAccessToBootstrapState: the CI roles can never read, write or
#     delete this root's own state (bootstrap/*). That state defines the CI
#     roles' permissions, so CI must not be able to tamper with it. The roles'
#     IAM policies don't grant it either; this explicit deny is a second layer.
data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyDeleteBucket"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket"]
    resources = [aws_s3_bucket.state.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "DenyCiAccessToBootstrapState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["${aws_s3_bucket.state.arn}/bootstrap/*"]

    principals {
      type = "AWS"
      identifiers = [
        aws_iam_role.ci.arn,
        aws_iam_role.plan.arn,
        aws_iam_role.release.arn,
      ]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  # Public access block settings must be in place before a policy is attached.
  depends_on = [aws_s3_bucket_public_access_block.state]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# This account has GitHub's "immutable subject" OIDC feature on by default
# (confirmed via `gh api repos/hacka-tron/basel.engineering/actions/oidc/customization/sub`,
# which returned use_immutable_subject: true) - the sub claim bakes in the
# permanent numeric owner/repo IDs instead of the plain "owner/repo" name,
# specifically so a repo rename or a name later reused by a different repo
# can't collide with an old trust policy. Confirmed empirically: a real
# workflow run's decoded OIDC token showed
# "repo:hacka-tron@14956857/basel.engineering@1394092219:environment:release",
# not the plain-name format every trust condition below originally assumed
# (and which silently never worked, since nothing had actually exercised
# these roles via a real Actions run until this was discovered). Every OIDC
# trust condition in this file uses this prefix, not a hardcoded plain-name
# string.
locals {
  github_oidc_subject_prefix = "repo:hacka-tron@14956857/basel.engineering@1394092219"

  # State Manager associations of the project's own (glassbox-*) documents.
  ssm_association_resources = [
    "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:association/*",
    "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*",
  ]
}

# Applies infrastructure changes after approval of the terraform-prod GitHub
# environment.
data "aws_iam_policy_document" "ci_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_oidc_subject_prefix}:environment:terraform-prod"]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "glassbox-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_trust.json
}

data "aws_iam_policy_document" "ci" {
  statement {
    sid       = "RegionalInfrastructure"
    effect    = "Allow"
    actions   = ["ec2:*", "rds:*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # infra/envs/prod's `registry` module manages the ECR repository as a real
  # resource in that state, so every apply refreshes it - not just infra
  # changes that touch it directly. glassbox-ci-plan (read-only) already has
  # narrower describe-only access; this is the actual apply role, so it
  # needs full lifecycle management, not just reads. Found the hard way: the
  # first real automated apply after adding the registry module failed with
  # AccessDeniedException on ecr:DescribeRepositories.
  statement {
    sid       = "ManageGlassboxEcrRepository"
    effect    = "Allow"
    actions   = ["ecr:*"]
    resources = ["arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox"]
  }

  statement {
    sid    = "RegionalParameterStore"
    effect = "Allow"
    actions = [
      "ssm:AddTagsToResource",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "ssm:DescribeParameters",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:ListTagsForResource",
      "ssm:PutParameter",
      "ssm:RemoveTagsFromResource",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # infra/modules/compute/zram.tf: the glassbox-zram-swap Command document
  # and the State Manager association that runs it on the node.
  #
  # Scope: documents are limited to the glassbox- name prefix and can't be
  # shared (no ModifyDocumentPermission). Create/UpdateAssociation are also
  # authorized against the document they name, so only glassbox-* documents
  # can be associated (never AWS-RunShellScript). Associations are scoped by
  # tag (SSM supports aws:RequestTag on CreateAssociation and
  # aws:ResourceTag on Describe/Update/DeleteAssociation): only associations
  # tagged project=glassbox can be created, read, changed or deleted.
  #
  # Residual risk: IAM checks InstanceIds targets against the instance
  # (tag-scoped below), but tag-based targets are not checked against any
  # resource, so this role could still target a glassbox-* document by tag at
  # another instance in this region. And the
  # pre-existing RegionalParameterStore statement grants
  # ssm:AddTagsToResource region-wide, so the role could tag a foreign
  # association project=glassbox to bring it into scope. Accepted: the
  # account has exactly one instance and no other associations, and this
  # role already has ec2:* in the region (it could equally rewrite the
  # instance's user_data).
  statement {
    sid    = "ManageProjectSsmDocuments"
    effect = "Allow"
    actions = [
      "ssm:CreateDocument",
      "ssm:DeleteDocument",
      "ssm:DescribeDocument",
      "ssm:DescribeDocumentPermission",
      "ssm:GetDocument",
      "ssm:ListDocumentVersions",
      "ssm:UpdateDocument",
      "ssm:UpdateDocumentDefaultVersion",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*"]
  }

  # Associations must carry the project=glassbox tag (Terraform's
  # default_tags; the provider sends it in CreateAssociation's Tags).
  statement {
    sid       = "CreateTaggedProjectSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:CreateAssociation"]
    resources = local.ssm_association_resources

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/project"
      values   = ["glassbox"]
    }
  }

  # Create/UpdateAssociation are also authorized against each target
  # instance (the first live apply failed with AccessDenied on
  # instance/i-...), so allow them only on instances tagged project=glassbox.
  statement {
    sid       = "AssociateTaggedProjectInstances"
    effect    = "Allow"
    actions   = ["ssm:CreateAssociation", "ssm:UpdateAssociation"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/project"
      values   = ["glassbox"]
    }
  }

  statement {
    sid    = "ManageTaggedProjectSsmAssociations"
    effect = "Allow"
    actions = [
      "ssm:DeleteAssociation",
      "ssm:DescribeAssociation",
      "ssm:UpdateAssociation",
    ]
    # The condition is checked per resource: the association and, for
    # UpdateAssociation, the glassbox-* document it names (also tagged
    # project=glassbox by default_tags).
    resources = local.ssm_association_resources

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["glassbox"]
    }
  }

  statement {
    sid       = "ListSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:ListAssociations", "ssm:ListAssociationVersions", "ssm:ListDocuments"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid    = "ProjectRolesAndProfiles"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreateRole",
      "iam:DeleteInstanceProfile",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetInstanceProfile",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagRole",
      "iam:UntagInstanceProfile",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
    ]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:role/glassbox-*",
      "arn:aws:iam::${var.aws_account_id}:instance-profile/glassbox-*",
    ]
  }

  statement {
    sid    = "ProjectPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListEntitiesForPolicy",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = ["arn:aws:iam::${var.aws_account_id}:policy/glassbox-*"]
  }

  statement {
    sid       = "PassProjectEc2Roles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.aws_account_id}:role/glassbox-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    # List only what the envs/prod backend needs: its own prefix, the exact
    # state key (the backend lists with prefix = key), and the default
    # workspace-enumeration prefix env:/ (empty, workspaces are not used).
    # Never bootstrap/.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "envs/prod",
        "envs/prod/*",
        "env:/",
        "env:/*",
      ]
    }
  }

  statement {
    sid       = "StateBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  # Scoped to the production state prefix only (state object + S3-native
  # .tflock lockfile). Deliberately NOT the whole bucket: CI must not be able
  # to read or change bootstrap/terraform.tfstate, the state that defines
  # CI's own permissions.
  statement {
    sid       = "ReadWriteProductionStateAndLockfile"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/*"]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "glassbox-ci-deploy"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}

# Terraform plans run only after approval of the terraform-plan GitHub
# environment. The role can read production resources and state, and can write
# only the S3 lockfile needed by Terraform's remote backend.
data "aws_iam_policy_document" "plan_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_oidc_subject_prefix}:environment:terraform-plan"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = "glassbox-ci-plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust.json
}

data "aws_iam_policy_document" "plan" {
  statement {
    sid       = "DescribeRegionalInfrastructure"
    effect    = "Allow"
    actions   = ["ec2:Describe*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ReadProjectIam"
    effect    = "Allow"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  statement {
    sid       = "DescribeParameters"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid     = "ReadProjectParameters"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/glassbox/*",
    ]
  }

  # Refresh of the glassbox-zram-swap document and its association
  # (infra/modules/compute/zram.tf), including their tags.
  statement {
    sid    = "ReadProjectSsmDocuments"
    effect = "Allow"
    actions = [
      "ssm:DescribeDocument",
      "ssm:DescribeDocumentPermission",
      "ssm:GetDocument",
      "ssm:ListTagsForResource",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*"]
  }

  statement {
    sid       = "ReadTaggedProjectSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:DescribeAssociation", "ssm:ListTagsForResource"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:association/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["glassbox"]
    }
  }

  statement {
    sid       = "ListSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:ListAssociations", "ssm:ListAssociationVersions", "ssm:ListDocuments"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ReadPublicAmiParameter"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"]
  }

  statement {
    sid     = "ReadProjectEcrRepository"
    effect  = "Allow"
    actions = ["ecr:DescribeRepositories", "ecr:GetLifecyclePolicy", "ecr:ListTagsForResource"]
    resources = [
      "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox",
    ]
  }

  statement {
    sid       = "DecryptProjectParametersViaSsm"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    # List only what the envs/prod backend needs: its own prefix, the exact
    # state key (the backend lists with prefix = key), and the default
    # workspace-enumeration prefix env:/ (empty, workspaces are not used).
    # Never bootstrap/.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "envs/prod",
        "envs/prod/*",
        "env:/",
        "env:/*",
      ]
    }
  }

  statement {
    sid       = "StateBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "ReadProductionState"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/terraform.tfstate"]
  }

  statement {
    sid       = "ManageProductionStateLock"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/terraform.tfstate.tflock"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "glassbox-ci-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# Pushes the application image to ECR on every merge to main. Scoped to a
# dedicated "release" GitHub environment (not the terraform-plan/prod ones -
# this never touches infrastructure, only an already-tested image).
data "aws_iam_policy_document" "release_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${local.github_oidc_subject_prefix}:environment:release"]
    }
  }
}

resource "aws_iam_role" "release" {
  name               = "glassbox-ci-release"
  assume_role_policy = data.aws_iam_policy_document.release_trust.json
}

data "aws_iam_policy_document" "release" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # ECR requires this action to be unscoped
  }

  statement {
    sid    = "PushGlassboxImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox"]
  }
}

resource "aws_iam_role_policy" "release" {
  name   = "glassbox-ci-release"
  role   = aws_iam_role.release.id
  policy = data.aws_iam_policy_document.release.json
}
