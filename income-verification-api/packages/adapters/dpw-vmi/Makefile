DPW_VMI_ADAPTER_DIR := packages/adapters/dpw-vmi
DPW_VMI_POST_WEBHOOK := $(DPW_VMI_ADAPTER_DIR)/scripts/post-webhook
DPW_VMI_LOCAL_S3_OBJECT := $(API_DIR)/$(DPW_VMI_ADAPTER_DIR)/scripts/local-s3-object

.PHONY: vmi-report-submission
vmi-report-submission: ## post a sample VMI ReportSubmission webhook event
	$(LOCAL_RUN) $(DPW_VMI_POST_WEBHOOK) \
		$(DPW_VMI_ADAPTER_DIR)/scripts/report-submission.json

.PHONY: vmi-transmission-complete
vmi-transmission-complete: ## post a sample VMI TransmissionComplete webhook event
	$(LOCAL_RUN) $(DPW_VMI_POST_WEBHOOK) \
		$(DPW_VMI_ADAPTER_DIR)/scripts/transmission-complete.json

.PHONY: vmi-webhook-events
vmi-webhook-events: vmi-report-submission vmi-transmission-complete ## post both sample VMI webhook events

.PHONY: vmi-webhook-listener-mock
vmi-webhook-listener-mock: ## run a local receiver for testing forwarded VMI webhooks
	$(COMPOSE) up program-webhook-mock -d

.PHONY: vmi-s3-upload
vmi-s3-upload: fakecloud-up ## upload a generated .tar.gz containing an empty .pdf to OBJECT_KEY=<object-key> in the local VMI inbox
	@$(DPW_VMI_LOCAL_S3_OBJECT) upload "$$OBJECT_KEY"

.PHONY: vmi-s3-head
vmi-s3-head: ## inspect OBJECT_KEY=<object-key> in the local VMI inbox
	@$(DPW_VMI_LOCAL_S3_OBJECT) head "$$OBJECT_KEY"
