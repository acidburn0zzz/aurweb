from aurweb import prometheus


def clear_metrics():
    prometheus.PACKAGES.clear()
    prometheus.REQUESTS.clear()
    prometheus.SEARCH_REQUESTS.clear()
    prometheus.USERS.clear()
