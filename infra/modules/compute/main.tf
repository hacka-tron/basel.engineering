terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66.0"
    }
  }
}

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  # https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6
  # Fetched 2026-09-29. Refresh manually when Cloudflare changes its ranges.
  cloudflare_ipv4_cidrs = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
  ]
  cloudflare_ipv6_cidrs = [
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
    "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
    "2c0f:f248::/32",
  ]

  # Live get-inference-profile responses in us-east-1 on 2026-09-29 list
  # us-east-1, us-east-2, and us-west-2 for both US profiles below.
  bedrock_destination_regions = ["us-east-1", "us-east-2", "us-west-2"]
  bedrock_profiles = {
    NovaLite = {
      profile_id = "us.amazon.nova-lite-v1:0"
      model_id   = "amazon.nova-lite-v1:0"
    }
    ClaudeHaiku = {
      profile_id = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
      model_id   = "anthropic.claude-haiku-4-5-20251001-v1:0"
    }
  }
}

resource "aws_security_group" "glassbox" {
  name        = "glassbox-origin"
  description = "Cloudflare HTTP and HTTPS access to the Glassbox origin"
  vpc_id      = var.vpc_id

  tags = { Name = "glassbox-origin" }
}

resource "aws_vpc_security_group_ingress_rule" "cloudflare_ipv4" {
  for_each          = { for pair in setproduct([80, 443], local.cloudflare_ipv4_cidrs) : "${pair[0]}-${pair[1]}" => pair }
  security_group_id = aws_security_group.glassbox.id
  ip_protocol       = "tcp"
  from_port         = each.value[0]
  to_port           = each.value[0]
  cidr_ipv4         = each.value[1]
  description       = "Cloudflare ${each.value[0]}"
}

resource "aws_vpc_security_group_ingress_rule" "cloudflare_ipv6" {
  for_each          = { for pair in setproduct([80, 443], local.cloudflare_ipv6_cidrs) : "${pair[0]}-${pair[1]}" => pair }
  security_group_id = aws_security_group.glassbox.id
  ip_protocol       = "tcp"
  from_port         = each.value[0]
  to_port           = each.value[0]
  cidr_ipv6         = each.value[1]
  description       = "Cloudflare ${each.value[0]}"
}

resource "aws_vpc_security_group_egress_rule" "all_ipv4" {
  security_group_id = aws_security_group.glassbox.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all_ipv6" {
  security_group_id = aws_security_group.glassbox.id
  ip_protocol       = "-1"
  cidr_ipv6         = "::/0"
}

data "aws_iam_policy_document" "instance_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "glassbox-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_trust.json
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "ReadGlassboxParameters"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/glassbox/*"]
  }

  statement {
    sid     = "InvokeTitanEmbedding"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0",
    ]
  }

  dynamic "statement" {
    for_each = local.bedrock_profiles
    content {
      sid     = "Invoke${statement.key}Profile"
      actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = [
        "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:inference-profile/${statement.value.profile_id}",
      ]
    }
  }

  dynamic "statement" {
    for_each = local.bedrock_profiles
    content {
      sid     = "Invoke${statement.key}ModelsThroughProfile"
      actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = [
        for region in local.bedrock_destination_regions :
        "arn:aws:bedrock:${region}::foundation-model/${statement.value.model_id}"
      ]
      condition {
        test     = "StringEquals"
        variable = "bedrock:InferenceProfileArn"
        values = [
          "arn:aws:bedrock:${var.aws_region}:${var.aws_account_id}:inference-profile/${statement.value.profile_id}",
        ]
      }
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "glassbox-instance-access"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "glassbox-instance"
  role = aws_iam_role.instance.name
}

resource "aws_instance" "glassbox" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = "t4g.small"
  subnet_id              = var.public_subnet_id
  vpc_security_group_ids = [aws_security_group.glassbox.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data              = templatefile("${path.module}/user_data.sh", {})

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "glassbox" }

  depends_on = [aws_iam_role_policy.instance, aws_iam_role_policy_attachment.ssm_core]
}

resource "aws_eip" "glassbox" {
  domain   = "vpc"
  instance = aws_instance.glassbox.id

  tags = { Name = "glassbox" }
}
