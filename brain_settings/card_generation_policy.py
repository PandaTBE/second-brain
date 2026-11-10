"""Provider-specific eligibility for automatic card generation."""


def manual_cards_only(settings_service):
    settings = settings_service.store.load()
    return settings.provider == "gigacode_cli" and not settings.gigacode_auto_cards


def execution_mode(processing_scope):
    return "local" if processing_scope == "model_local" else "cloud"


def automatic_cards_enabled(settings_service, schedule):
    return (not manual_cards_only(settings_service) and not schedule.paused
            and schedule.process_cards and (schedule.enabled or schedule.auto_import_enabled))
