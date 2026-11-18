import logging
from typing import Dict, Any
from copy import deepcopy
from src.tools.chinese_demo_customers import CUSTOMERS

logger = logging.getLogger("supportgpt.tools.crm")

class CRMTool:
    """模拟 CRM 客户画像查询，返回等级和历史概况。"""
    def __init__(self):
        # In-memory customer directory
        self.customers: Dict[str, Dict[str, Any]] = {
            "cust_101": {
                "customer_id": "cust_101",
                "name": "Jane Doe",
                "tier": "VIP",
                "open_tickets_count": 2,
                "email": "jane.doe@enterprise.com"
            },
            "cust_102": {
                "customer_id": "cust_102",
                "name": "John Smith",
                "tier": "Standard",
                "open_tickets_count": 0,
                "email": "john.smith@gmail.com"
            },
            "cust_103": {
                "customer_id": "cust_103",
                "name": "Acme Corp (Rep: Alice)",
                "tier": "Enterprise",
                "open_tickets_count": 5,
                "email": "alice@acme.org"
            }
        }
        self.customers.update(deepcopy(CUSTOMERS))

    def get_customer_profile(self, customer_id: str) -> Dict[str, Any]:
        """Fetch customer profile data. Returns defaults if user doesn't exist."""
        logger.info(f"Looking up customer ID: {customer_id} in CRM database.")
        if customer_id in self.customers:
            return self.customers[customer_id]
        
        # Default placeholder for unknown customers
        return {
            "customer_id": customer_id,
            "name": "Unknown Customer",
            "tier": "Standard",
            "open_tickets_count": 1,
            "email": f"{customer_id}@example.com"
        }

crm_tool = CRMTool()
