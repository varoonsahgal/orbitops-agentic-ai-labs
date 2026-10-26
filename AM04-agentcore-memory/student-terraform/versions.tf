terraform {
  required_version = ">= 1.6"
  required_providers {
    aws      = { source = "hashicorp/aws", version = ">= 6.27" }
    archive  = { source = "hashicorp/archive", version = ">= 2.4" }
    external = { source = "hashicorp/external", version = ">= 2.3" }
    time     = { source = "hashicorp/time", version = ">= 0.12" }
  }
}
