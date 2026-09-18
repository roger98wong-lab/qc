import { useEffect, useMemo, useState } from 'react'
import { Button, Drawer, Image, Typography } from 'antd'
import { helpSectionsForRole, type HelpRole } from '../help/content'

const { Title, Paragraph, Text } = Typography
const HELP_SECTION_KEY = 'qc.help.lastSection'

export default function HelpDrawer({
  open, onClose, role,
}: {
  open: boolean
  onClose: () => void
  role: HelpRole
}) {
  const sections = useMemo(() => helpSectionsForRole(role), [role])
  const [activeId, setActiveId] = useState(sections[0]?.id || 'overview')

  useEffect(() => {
    if (!open) return
    const saved = localStorage.getItem(HELP_SECTION_KEY)
    const next = sections.find(section => section.id === saved)?.id || sections[0]?.id
    if (next) setActiveId(next)
  }, [open, sections])

  const active = sections.find(section => section.id === activeId) || sections[0]
  const go = (id: string) => {
    setActiveId(id)
    localStorage.setItem(HELP_SECTION_KEY, id)
    document.getElementById(`qc-help-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <Drawer
      title="使用说明"
      placement="right"
      width="min(880px, 96vw)"
      open={open}
      onClose={onClose}
      destroyOnClose={false}
      className="qc-help-drawer"
      keyboard
      maskClosable
    >
      <div className="qc-help-layout">
        <nav className="qc-help-toc" aria-label="使用说明目录">
          {sections.map(section => (
            <Button
              key={section.id}
              type={section.id === active?.id ? 'primary' : 'text'}
              size="small"
              className="qc-help-toc-item"
              onClick={() => go(section.id)}
            >
              {section.title}
            </Button>
          ))}
        </nav>
        <div className="qc-help-body">
          {sections.map(section => (
            <section key={section.id} id={`qc-help-${section.id}`} className="qc-help-section">
              <Title level={4}>{section.title}</Title>
              <Paragraph>{section.summary}</Paragraph>
              <Text strong>你要点哪里</Text>
              <ol className="qc-help-steps">
                {section.steps.map(step => <li key={step}>{step}</li>)}
              </ol>
              <figure className="qc-help-figure">
                <Image src={section.image} alt={section.imageAlt} />
                <figcaption>{section.imageAlt}</figcaption>
              </figure>
              {(section.extraImages || []).map(image => (
                <figure key={image.src} className="qc-help-figure">
                  <Image src={image.src} alt={image.alt} />
                  <figcaption>{image.alt}</figcaption>
                </figure>
              ))}
              {section.notes?.length ? (
                <>
                  <Text strong>注意</Text>
                  <ul className="qc-help-notes">
                    {section.notes.map(note => <li key={note}>{note}</li>)}
                  </ul>
                </>
              ) : null}
            </section>
          ))}
        </div>
      </div>
    </Drawer>
  )
}
