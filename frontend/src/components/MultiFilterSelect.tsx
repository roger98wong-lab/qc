import { Checkbox, Select, type SelectProps } from 'antd'

type Value = string | number

export default function MultiFilterSelect({ value, options, className, style, ...rest }: SelectProps) {
  const selected = Array.isArray(value) ? value : []
  return (
    <Select
      mode="multiple"
      allowClear
      showSearch
      maxTagCount="responsive"
      optionFilterProp="label"
      popupMatchSelectWidth={280}
      value={selected}
      options={options}
      optionRender={option => (
        <span className="qc-multi-option">
          <Checkbox checked={selected.includes((option.value ?? option.data?.value) as Value)} />
          <span>{option.label ?? option.data?.label}</span>
        </span>
      )}
      className={['qc-multi-filter-select', className].filter(Boolean).join(' ')}
      style={{ minWidth: 200, ...style }}
      {...rest}
    />
  )
}