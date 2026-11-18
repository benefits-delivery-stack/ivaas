PLATFORM_DIR := ivaas-platform
ADAPTERS_CHECKOUT ?= $(abspath ../iv-colorado-adapters)
ADAPTERS_ENV ?= ../../.envs/.local/.adapters
SECRETS_ENV ?= ../../.envs/.local/.secrets
ADAPTER_WHEELS_DIR := $(abspath custom-adapters/wheels)

export IVAAS_DEPLOYMENT_SETTINGS := deployment.settings
export PYTHONPATH := $(CURDIR)

PLATFORM_MAKE := $(MAKE) -C $(PLATFORM_DIR) \
	ADAPTERS_CHECKOUT=$(ADAPTERS_CHECKOUT) \
	ADAPTERS_ENV=$(ADAPTERS_ENV) \
	SECRETS_ENV=$(SECRETS_ENV) \
	WHEELS_DIR=$(ADAPTER_WHEELS_DIR)
.DEFAULT_GOAL := help

Makefile: ;

%:
	@$(PLATFORM_MAKE) $@
