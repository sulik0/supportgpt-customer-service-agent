import logging
import datetime
from typing import List, Dict, Any
from copy import deepcopy
from src.tools.chinese_demo_customers import ORDERS

logger = logging.getLogger("supportgpt.tools.order_mgmt")

class OrderManagementTool:
    """模拟 OMS 订单查询和退款资格初筛能力。"""
    def __init__(self):
        # Sample order histories
        self.orders: Dict[str, List[Dict[str, Any]]] = {
            "cust_101": [
                {
                    "order_id": "ORD-7001",
                    "status": "delivered",
                    "items": ["Enterprise SaaS User Pack (10)"],
                    "total_amount": 150.00,
                    "currency": "USD",
                    "order_date": datetime.datetime.utcnow() - datetime.timedelta(days=20)
                }
            ],
            "cust_102": [
                {
                    "order_id": "ORD-8002",
                    "status": "shipped",
                    "items": ["Developer API Key Pack"],
                    "total_amount": 25.00,
                    "currency": "USD",
                    "order_date": datetime.datetime.utcnow() - datetime.timedelta(days=1)
                }
            ],
            "cust_103": [
                {
                    "order_id": "ORD-9003",
                    "status": "delivered",
                    "items": ["Dedicated AWS Gateway Cluster", "Enterprise Premium support SLA addon"],
                    "total_amount": 5400.00,
                    "currency": "USD",
                    "order_date": datetime.datetime.utcnow() - datetime.timedelta(days=5)
                }
            ]
        }
        self.orders.update(deepcopy(ORDERS))

    def get_order_history(self, customer_id: str) -> List[Dict[str, Any]]:
        """Fetch e-commerce order details for a customer."""
        logger.info(f"Looking up order transactions for customer: {customer_id}")
        return self.orders.get(customer_id, [])

order_mgmt_tool = OrderManagementTool()
