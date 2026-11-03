// Read-only prompts grounded in src/tools/chinese_demo_customers.py.
// These are test inputs and inspection hints, never prerecorded workflow results.
export const WORKFLOW_EXAMPLES = [
  {
    id: 'shipment', customerId: 'cust_201', customerName: '张晓雨', title: '订单与物流联合查询',
    question: '我的订单 ORD-12001 还没有收到，请查询订单状态和物流记录，说明延迟原因和下一步。',
    tools: ['orders.get_order_history', 'shipping.get_shipments'],
    inspect: '核对订单已发货、物流延迟和承运商核查信息；不要把核查中说成已送达。'
  },
  {
    id: 'invoice', customerId: 'cust_203', customerName: '星河科技 / 陈晨', title: '订单付款与发票查询',
    question: '请查询订单 ORD-12003 的付款记录和发票进度，告诉我是否已付款、发票是否已开好。',
    tools: ['orders.get_order_history', 'billing.get_payment_invoices'],
    inspect: '核对已付款、发票处理中；查询不应变成开票或退款操作。'
  },
  {
    id: 'warranty', customerId: 'cust_202', customerName: '李明', title: '设备保修权益查询',
    question: '订单 ORD-12002 的网关设备出现故障，请查询保修权益，并说明维修前还需要确认什么。',
    tools: ['warranty.get_entitlements'],
    inspect: '核对模拟保修权益有效、尚未创建维修申请；具体故障、范围和费用仍需核实。'
  }
];
