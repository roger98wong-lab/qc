import { Modal, Button, Space, Typography, Tooltip } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import type { ReactNode } from 'react'

const { Text } = Typography

export default function DetailModalLayout({
  open, title, recordLabel, reviewerLabel, navigationLabel = '当前记录',
  index, total, onPrevious, onNext, onClose, children, className,
}: {
  open: boolean
  title: ReactNode
  recordLabel?: ReactNode
  reviewerLabel?: ReactNode
  navigationLabel?: string
  index?: number
  total?: number
  onPrevious?: () => void
  onNext?: () => void
  onClose: () => void
  children?: ReactNode
  className?: string
}) {
  const showNav = Boolean(onPrevious && onNext && typeof index === 'number' && typeof total === 'number')
  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      centered
      keyboard
      maskClosable
      closable={false}
      closeIcon={null}
      destroyOnClose={false}
      width="min(1360px, calc(100vw - 32px))"
      className={`qc-detail-modal ${className || ''}`}
      title={
        <div className="qc-detail-modal-header">
          <div className="qc-detail-modal-heading">
            <span className="qc-detail-modal-title">{title}</span>
            {recordLabel ? <Text type="secondary" className="qc-detail-modal-record">{recordLabel}</Text> : null}
            <Text type="secondary" className="qc-detail-modal-reviewer">处理人：{reviewerLabel || '未分配'}</Text>
          </div>
        </div>
      }
    >
      <div className="qc-detail-modal-shell">
        <div className="qc-detail-modal-body">{children}</div>
        <div className="qc-detail-modal-footer" aria-label={navigationLabel}>
          <Space size={8} wrap className="qc-detail-modal-navigation">
            {showNav && (
              <>
                <Button size="small" onClick={onPrevious} disabled={(index || 0) <= 0}>上一条</Button>
                <Text type="secondary">{(index || 0) + 1} / {total || '无'}</Text>
                <Button size="small" onClick={onNext} disabled={(index || 0) >= (total || 0) - 1}>下一条</Button>
              </>
            )}
            <Tooltip title="关闭（不保存）">
              <Button size="small" icon={<CloseOutlined />} onClick={onClose}>关闭</Button>
            </Tooltip>
          </Space>
        </div>
      </div>
    </Modal>
  )
}
