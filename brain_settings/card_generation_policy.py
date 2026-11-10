"""Provider-specific eligibility for automatic card generation."""


def manual_cards_only(settings_service):
    return settings_service.store.load().provider == "gigacode_cli"


def execution_mode(processing_scope):
    return "local" if processing_scope == "model_local" else "cloud"


def automatic_cards_enabled(settings_service, schedule):
    return (not manual_cards_only(settings_service) and not schedule.paused
            and schedule.process_cards and (schedule.enabled or schedule.auto_import_enabled))
